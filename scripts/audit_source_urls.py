"""Audit URLs in scholar dossiers that came from LLM grounded search.

Scans every scholar's news.jsonl, startups.json, patents.json, red_flags.jsonl,
fetches each URL over HTTP with redirect tracking, and classifies the result.

Usage:
    python scripts/audit_source_urls.py           # audit + write report
    python scripts/audit_source_urls.py --limit 50  # smoke test

Writes two artefacts under data/audits/:
    source_urls_report.json  - machine-readable full records
    source_urls_summary.txt  - human summary
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHOLARS_DIR = REPO_ROOT / "data" / "scholars"
OUT_DIR = REPO_ROOT / "data" / "audits"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
TIMEOUT = 15.0
CONCURRENCY = 20


# ---------------------------------------------------------------------------
# 1. ENUMERATE URLS
# ---------------------------------------------------------------------------

@dataclass
class UrlRef:
    scholar: str
    file: str            # relative name e.g. "news.jsonl"
    field: str           # JSON field that holds the url
    url: str
    context: str         # title / one_liner / claim


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def _collect_for_scholar(scholar_dir: Path) -> list[UrlRef]:
    sid = scholar_dir.name
    refs: list[UrlRef] = []

    # news.jsonl — {url, title}
    news = scholar_dir / "news.jsonl"
    if news.exists():
        for row in _iter_jsonl(news):
            url = (row.get("url") or "").strip()
            if url:
                refs.append(UrlRef(
                    sid, "news.jsonl", "url", url,
                    row.get("title") or ""))

    # red_flags.jsonl — {source_url, claim}
    flags = scholar_dir / "red_flags.jsonl"
    if flags.exists():
        for row in _iter_jsonl(flags):
            url = (row.get("source_url") or "").strip()
            if url:
                refs.append(UrlRef(
                    sid, "red_flags.jsonl", "source_url", url,
                    (row.get("claim") or "")[:120]))

    # startups.json — items[].url
    startups = _load_json(scholar_dir / "startups.json")
    if isinstance(startups, dict):
        for item in startups.get("items", []):
            url = (item.get("url") or "").strip()
            if url:
                refs.append(UrlRef(
                    sid, "startups.json", "items[].url", url,
                    item.get("name") or ""))

    # patents.json — items[].url
    patents = _load_json(scholar_dir / "patents.json")
    if isinstance(patents, dict):
        for item in patents.get("items", []):
            url = (item.get("url") or "").strip()
            if url:
                refs.append(UrlRef(
                    sid, "patents.json", "items[].url", url,
                    item.get("title") or ""))

    return refs


def enumerate_all() -> list[UrlRef]:
    refs: list[UrlRef] = []
    for sdir in sorted(SCHOLARS_DIR.iterdir()):
        if not sdir.is_dir():
            continue
        if sdir.name.startswith("__"):
            continue  # skip synthetic chat test scholars
        refs.extend(_collect_for_scholar(sdir))
    return refs


# ---------------------------------------------------------------------------
# 2. HTTP CHECK
# ---------------------------------------------------------------------------

def _path_norm(url: str) -> str:
    try:
        p = urlparse(url)
        path = (p.path or "/").rstrip("/")
        return path or "/"
    except Exception:
        return "/"


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _classify(
    url: str,
    status: int | None,
    final_url: str | None,
    err: str | None,
) -> str:
    if err:
        if "timeout" in err.lower() or "timed out" in err.lower():
            return "timeout"
        if "name" in err.lower() or "dns" in err.lower() or "nodename" in err.lower():
            return "dns_error"
        if "ssl" in err.lower() or "certificate" in err.lower():
            return "ssl_error"
        return "conn_error"

    if status is None:
        return "conn_error"

    if status == 404:
        return "404"
    if status == 410:
        return "gone"
    if status == 403:
        return "403_forbidden"
    if 400 <= status < 500:
        return "other_4xx"
    if status >= 500:
        return "5xx"

    # 2xx / 3xx landed somewhere. Did we collapse to the homepage of a
    # different/same host, while the original URL had a real path?
    orig_path = _path_norm(url)
    final_path = _path_norm(final_url or url)
    orig_host = _host(url)
    final_host = _host(final_url or url)

    orig_had_path = orig_path not in ("/", "")
    final_is_root = final_path in ("/", "")

    if orig_had_path and final_is_root:
        # Redirected from /foo/bar to /
        return "homepage_redirect"
    if orig_had_path and final_host != orig_host and final_is_root:
        # Cross-domain redirect to homepage
        return "homepage_redirect"
    return "ok"


@dataclass
class UrlResult:
    ref: UrlRef
    status: int | None
    final_url: str | None
    error: str | None
    classification: str
    elapsed_ms: int


async def _check_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, ref: UrlRef,
) -> UrlResult:
    url = ref.url
    start = time.monotonic()
    status: int | None = None
    final_url: str | None = None
    err: str | None = None

    async with sem:
        for method in ("HEAD", "GET"):
            try:
                resp = await client.request(
                    method, url, follow_redirects=True,
                    headers={"User-Agent": UA, "Accept": "*/*"},
                )
                status = resp.status_code
                final_url = str(resp.url)
                # If HEAD is 405/403/blocked, retry with GET
                if method == "HEAD" and status in (403, 405, 501):
                    continue
                break
            except httpx.HTTPError as exc:
                err = f"{type(exc).__name__}: {exc}"
                if method == "HEAD":
                    # retry with GET on transport errors too
                    err_head = err
                    err = None
                    continue
                else:
                    break
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                break

    elapsed = int((time.monotonic() - start) * 1000)
    cls = _classify(url, status, final_url, err)
    return UrlResult(ref, status, final_url, err, cls, elapsed)


async def check_all(refs: list[UrlRef]) -> list[UrlResult]:
    sem = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY * 2,
                          max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=TIMEOUT, limits=limits, http2=False,
        verify=True,
    ) as client:
        tasks = [_check_one(client, sem, r) for r in refs]
        results: list[UrlResult] = []
        total = len(tasks)
        done = 0
        for coro in asyncio.as_completed(tasks):
            r = await coro
            results.append(r)
            done += 1
            if done % 25 == 0 or done == total:
                print(f"  checked {done}/{total}", file=sys.stderr)
        return results


# ---------------------------------------------------------------------------
# 3. REPORT
# ---------------------------------------------------------------------------

def _bucket_order() -> list[str]:
    return [
        "ok",
        "homepage_redirect",
        "404",
        "gone",
        "403_forbidden",
        "other_4xx",
        "5xx",
        "timeout",
        "dns_error",
        "ssl_error",
        "conn_error",
    ]


def _summarize(results: list[UrlResult]) -> str:
    total = len(results)
    by_cls: dict[str, list[UrlResult]] = {}
    for r in results:
        by_cls.setdefault(r.classification, []).append(r)

    lines: list[str] = []
    lines.append(f"Total URLs checked: {total}")
    lines.append("")
    lines.append("Classification breakdown:")
    for key in _bucket_order():
        n = len(by_cls.get(key, []))
        if n:
            pct = n / total * 100
            lines.append(f"  {key:<20} {n:>5}  ({pct:5.1f}%)")

    # Per-source breakdown
    lines.append("")
    lines.append("By source file (failure rate = anything not 'ok'):")
    files: dict[str, list[UrlResult]] = {}
    for r in results:
        files.setdefault(r.ref.file, []).append(r)
    for fname in sorted(files):
        rows = files[fname]
        bad = [r for r in rows if r.classification != "ok"]
        rate = len(bad) / len(rows) * 100 if rows else 0.0
        lines.append(f"  {fname:<22} {len(rows):>4} total, "
                     f"{len(bad):>4} failing ({rate:5.1f}%)")
        # breakdown per classification for this file
        cls_counts: dict[str, int] = {}
        for r in rows:
            cls_counts[r.classification] = cls_counts.get(r.classification, 0) + 1
        for key in _bucket_order():
            if cls_counts.get(key, 0) and key != "ok":
                lines.append(f"      - {key}: {cls_counts[key]}")

    # Per-scholar breakdown
    lines.append("")
    lines.append("By scholar (failures / total):")
    scholars: dict[str, list[UrlResult]] = {}
    for r in results:
        scholars.setdefault(r.ref.scholar, []).append(r)
    rows = []
    for sid, rs in scholars.items():
        bad = sum(1 for r in rs if r.classification != "ok")
        rows.append((sid, bad, len(rs)))
    rows.sort(key=lambda t: (-t[1], t[0]))
    for sid, bad, tot in rows:
        rate = bad / tot * 100 if tot else 0
        marker = "!!" if rate >= 50 else "  "
        lines.append(f"  {marker} {sid:<50} {bad:>3} / {tot:>3}  ({rate:5.1f}%)")

    # Domain failure rates (only domains with >=3 URLs)
    lines.append("")
    lines.append("By domain (n>=3, failure rate desc):")
    by_host: dict[str, list[UrlResult]] = {}
    for r in results:
        by_host.setdefault(_host(r.ref.url), []).append(r)
    drows = []
    for host, rs in by_host.items():
        if len(rs) < 3:
            continue
        bad = sum(1 for r in rs if r.classification != "ok")
        drows.append((host, bad, len(rs)))
    drows.sort(key=lambda t: (-t[1] / t[2], -t[2]))
    for host, bad, tot in drows[:40]:
        rate = bad / tot * 100 if tot else 0
        lines.append(f"  {host:<45} {bad:>3}/{tot:<3}  ({rate:5.1f}%)")

    # Examples per bucket
    lines.append("")
    lines.append("=== EXAMPLES ===")
    for key in _bucket_order():
        if key == "ok":
            continue
        rs = by_cls.get(key, [])
        if not rs:
            continue
        lines.append(f"\n-- {key} ({len(rs)}) --")
        for r in rs[:8]:
            extra = f" → {r.final_url}" if r.final_url and r.final_url != r.ref.url else ""
            lines.append(f"  [{r.ref.scholar}/{r.ref.file}] {r.ref.url}{extra}")
            if r.ref.context:
                lines.append(f"     · {r.ref.context[:140]}")
            if r.error:
                lines.append(f"     err: {r.error}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Check only first N URLs (for smoke testing)")
    ap.add_argument("--skip-check", action="store_true",
                    help="Only enumerate; do not perform HTTP checks")
    args = ap.parse_args()

    print("Enumerating URLs...", file=sys.stderr)
    refs = enumerate_all()
    print(f"  found {len(refs)} URLs across "
          f"{len({r.scholar for r in refs})} scholars", file=sys.stderr)
    # per-file counts
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.file] = counts.get(r.file, 0) + 1
    for f, n in sorted(counts.items()):
        print(f"    {f}: {n}", file=sys.stderr)

    if args.skip_check:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "source_urls_inventory.json"
        path.write_text(json.dumps([asdict(r) for r in refs], indent=2),
                        encoding="utf-8")
        print(f"Wrote inventory to {path}", file=sys.stderr)
        return 0

    if args.limit:
        refs = refs[: args.limit]
        print(f"Limiting to {len(refs)}", file=sys.stderr)

    print("Checking URLs...", file=sys.stderr)
    results = asyncio.run(check_all(refs))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = OUT_DIR / "source_urls_report.json"
    payload = [
        {
            "scholar": r.ref.scholar,
            "file": r.ref.file,
            "field": r.ref.field,
            "url": r.ref.url,
            "context": r.ref.context,
            "status": r.status,
            "final_url": r.final_url,
            "error": r.error,
            "classification": r.classification,
            "elapsed_ms": r.elapsed_ms,
        }
        for r in results
    ]
    report.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary = _summarize(results)
    (OUT_DIR / "source_urls_summary.txt").write_text(summary, encoding="utf-8")

    print()
    print(summary)
    print()
    print(f"Full JSON: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
