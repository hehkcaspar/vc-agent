"""Content-fit audit for Song Han's grounded-search URLs.

Stage 1: re-run all 4 academic sources for Song Han, collect 20ish items.
Stage 2: fetch each URL's page (HTML title + first 2000 chars of text).
Stage 3: ask Gemini flash to classify each page as
         relevant | generic_homepage | wrong_topic |
         search_no_results | search_with_hits | blocked | unclear.

Writes:
    data/audits/song_han_items.json        — raw items
    data/audits/song_han_content_fit.json  — with page snippet + classification
    data/audits/song_han_content_fit.txt   — human summary
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "audits"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.services.academic.llm_client import (  # noqa: E402
    generate_structured,
    grounded_search_json,
    await_url_refresh,
)
from pydantic import BaseModel  # noqa: E402

NAME = "Song Han"
AFFIL = "Massachusetts Institute of Technology"

NEWS_PROMPT = (
    f"Find news, press releases, or blog posts about academic researcher "
    f"**{NAME}** at {AFFIL} published since 2025-01-01. Include any story "
    f"where they are a named protagonist, OR the story is primarily about "
    f"a commercial venture they have founded or co-founded. Do NOT include "
    f"news about their university/institute/lab that does not name {NAME}; "
    f"news primarily about colleagues/students. Return ONLY a JSON array — "
    f"no prose, no markdown. Each item: title, url, published_date, source, "
    f"summary, category."
)
PATENTS_PROMPT = (
    f"Find U.S. or international patents where {NAME} (affiliated with "
    f"{AFFIL}) is a named inventor. Return a JSON array where each item "
    f"has: title, url (patent record URL), patent_number, inventors (list), "
    f"assignee, filing_date, grant_date (if granted), abstract (1-2 "
    f"sentences), jurisdiction."
)
STARTUPS_PROMPT = (
    f"Find commercial ventures (startups, companies) that {NAME} at {AFFIL} "
    f"has founded or co-founded. Return JSON array with items: name, url "
    f"(company homepage), founded_year, one_liner, current_status "
    f"(operating/acquired/defunct), funding_total_usd, last_funding_type, "
    f"acquirer, acquisition_date, notes."
)
RED_FLAGS_PROMPT = (
    f"Screen for business-reputation red flags on academic researcher "
    f"**{NAME}** at {AFFIL}: paper retractions, misconduct, lawsuits, "
    f"ethics concerns, grant clawbacks, sanctions, export-control "
    f"violations, political-risk exposure. Return JSON array with items: "
    f"category, severity (low/medium/high/critical), claim, source_url, "
    f"source_summary, affected_dimensions."
)


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


async def _collect_items() -> list[dict[str, Any]]:
    async def run_one(label: str, prompt: str, url_field: str = "url"):
        items = await grounded_search_json(prompt)
        await await_url_refresh(items)
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append({
                "source": label,
                "title": it.get("title") or it.get("name") or it.get("claim")
                         or "",
                "summary": (it.get("summary") or it.get("one_liner")
                            or it.get("source_summary") or it.get("abstract")
                            or ""),
                "url": it.get(url_field) or it.get("url") or "",
                "tier": it.get("_url_source"),
            })
        return out

    groups = await asyncio.gather(
        run_one("news", NEWS_PROMPT),
        run_one("patents", PATENTS_PROMPT),
        run_one("startups", STARTUPS_PROMPT),
        run_one("red_flags", RED_FLAGS_PROMPT, url_field="source_url"),
    )
    flat: list[dict[str, Any]] = []
    for g in groups:
        flat.extend(g)
    return flat


# ── Page fetcher ───────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(h: str, limit: int = 2500) -> str:
    # Strip scripts and styles first.
    h = re.sub(r"<script[\s\S]*?</script>", " ", h, flags=re.IGNORECASE)
    h = re.sub(r"<style[\s\S]*?</style>", " ", h, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", h)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def _extract_title(h: str) -> str:
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", h, flags=re.IGNORECASE)
    if not m:
        return ""
    return _WS_RE.sub(" ", html.unescape(m.group(1))).strip()[:300]


async def _fetch(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        resp = await client.get(
            url, follow_redirects=True,
            headers={"User-Agent": UA, "Accept": "text/html,*/*"},
        )
        body = resp.text or ""
        return {
            "status": resp.status_code,
            "final_url": str(resp.url),
            "page_title": _extract_title(body),
            "snippet": _strip_html(body),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": None,
            "final_url": None,
            "page_title": "",
            "snippet": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


# ── Content-fit classification via Gemini flash ────────────────────────

class _FitLabel(BaseModel):
    classification: str
    reason: str


_FIT_PROMPT = (
    "You are validating whether a URL actually leads to content about a "
    "specific item. Below is the item description and the text extracted "
    "from the fetched page.\n\n"
    "ITEM\n"
    "  source: {source}\n"
    "  scholar: Song Han (MIT)\n"
    "  title / name: {title}\n"
    "  summary: {summary}\n\n"
    "FETCHED PAGE\n"
    "  url: {url}\n"
    "  final_url: {final_url}\n"
    "  page_title: {page_title}\n"
    "  snippet: {snippet}\n\n"
    "Classify the page as exactly ONE of these keywords:\n"
    "- relevant: page is clearly about this specific item (the news event, "
    "this patent, this company, this red flag)\n"
    "- tangential: page mentions the scholar or topic in passing but is "
    "not the primary subject (e.g. a profile page that briefly references "
    "the event)\n"
    "- generic_homepage: page is a homepage, section hub, or category "
    "index — no specific coverage of the item\n"
    "- wrong_topic: page is about something entirely different\n"
    "- search_no_results: page is a Google/Bing search results page that "
    "shows no relevant hits\n"
    "- search_with_hits: page is a search results page with plausibly "
    "relevant hits that the user would click through\n"
    "- blocked: page returned a captcha, login wall, bot block, or error "
    "preventing content validation\n"
    "- unclear: insufficient evidence to classify\n\n"
    "Return a JSON object {{classification: <keyword>, reason: <one "
    "sentence>}}. The reason must be short — just enough to justify."
)


async def _classify(
    item: dict[str, Any], page: dict[str, Any],
) -> dict[str, Any]:
    url = item["url"]
    prompt = _FIT_PROMPT.format(
        source=item["source"],
        title=(item["title"] or "")[:200],
        summary=(item["summary"] or "")[:400],
        url=url[:250],
        final_url=(page.get("final_url") or "")[:250],
        page_title=(page.get("page_title") or "")[:200],
        snippet=(page.get("snippet") or "")[:2000],
    )
    try:
        label = await generate_structured(
            model="gemini-3-flash-preview",
            prompt_parts=[prompt],
            response_schema=_FitLabel,
        )
        return {
            "classification": label.classification,
            "reason": label.reason,
        }
    except Exception as exc:  # noqa: BLE001
        return {"classification": "classifier_error",
                "reason": f"{type(exc).__name__}: {exc}"}


# ── Main ───────────────────────────────────────────────────────────────

async def main() -> int:
    print("Stage 1: re-running Song Han sources (news/patents/startups/red_flags)",
          file=sys.stderr)
    t0 = time.monotonic()
    items = await _collect_items()
    print(f"  → {len(items)} items in {time.monotonic() - t0:.1f}s",
          file=sys.stderr)
    (OUT_DIR / "song_han_items.json").write_text(
        json.dumps(items, indent=2), encoding="utf-8"
    )

    print(f"Stage 2: fetching {len(items)} URLs", file=sys.stderr)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=8.0),
    ) as client:
        fetches = await asyncio.gather(
            *(_fetch(client, it["url"]) for it in items if it["url"]),
            return_exceptions=False,
        )

    paired = [
        (it, page) for it, page in zip(
            [i for i in items if i["url"]], fetches
        )
    ]

    print(f"Stage 3: classifying {len(paired)} pages (Gemini flash, concurrent)",
          file=sys.stderr)
    t2 = time.monotonic()
    # Concurrency cap so we don't spam the model provider.
    sem = asyncio.Semaphore(6)

    async def _classify_with_sem(item, page):
        async with sem:
            return await _classify(item, page)

    labels = await asyncio.gather(
        *(_classify_with_sem(it, p) for it, p in paired)
    )
    print(f"  → classified in {time.monotonic() - t2:.1f}s", file=sys.stderr)

    # Assemble final report.
    results = []
    for (it, page), label in zip(paired, labels):
        results.append({
            **it,
            "status": page.get("status"),
            "final_url": page.get("final_url"),
            "page_title": page.get("page_title"),
            "fetch_error": page.get("error"),
            "classification": label["classification"],
            "classification_reason": label["reason"],
        })

    (OUT_DIR / "song_han_content_fit.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )

    # ── Summary ──
    lines: list[str] = []
    lines.append(f"Content-fit audit — Song Han (MIT)")
    lines.append(f"Items: {len(results)}")
    lines.append("")
    lines.append("Classification distribution:")
    cls_ctr = Counter(r["classification"] for r in results)
    total = sum(cls_ctr.values())
    for cls, n in cls_ctr.most_common():
        pct = n / total * 100 if total else 0
        lines.append(f"  {cls:<22} {n:>3}  ({pct:5.1f}%)")

    lines.append("")
    lines.append("By source:")
    by_source: dict[str, Counter] = {}
    for r in results:
        by_source.setdefault(r["source"], Counter())[r["classification"]] += 1
    for src, ctr in sorted(by_source.items()):
        lines.append(f"  {src}:")
        for cls, n in ctr.most_common():
            lines.append(f"    {cls:<22} {n:>3}")

    lines.append("")
    lines.append("By URL tier (_url_source):")
    by_tier: dict[str, Counter] = {}
    for r in results:
        by_tier.setdefault(r["tier"] or "n/a", Counter())[r["classification"]] += 1
    for tier, ctr in sorted(by_tier.items()):
        total_tier = sum(ctr.values())
        lines.append(f"  {tier} (n={total_tier}):")
        for cls, n in ctr.most_common():
            lines.append(f"    {cls:<22} {n:>3}")

    lines.append("")
    lines.append("=== ALL ITEMS ===")
    for r in results:
        lines.append(f"\n[{r['source']}/{r['tier']}] {r['classification']}")
        lines.append(f"  title : {(r['title'] or '')[:120]}")
        lines.append(f"  url   : {(r['url'] or '')[:130]}")
        if r.get("final_url") and r["final_url"] != r["url"]:
            lines.append(f"  final : {r['final_url'][:130]}")
        if r.get("page_title"):
            lines.append(f"  ptitle: {r['page_title'][:120]}")
        lines.append(f"  reason: {r['classification_reason'][:200]}")
        if r.get("fetch_error"):
            lines.append(f"  err   : {r['fetch_error'][:120]}")

    report = "\n".join(lines)
    (OUT_DIR / "song_han_content_fit.txt").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
