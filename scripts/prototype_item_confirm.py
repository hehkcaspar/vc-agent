"""Prototype: per-item grounded-search confirmation.

Pipeline order (under test):

  grounded_search_json → **per-item confirm (this script)** → 3-tier fallback

For each item in ``data/audits/song_han_items.json``:

1. Call ``gemini-3.1-flash-lite-preview`` with ``google_search`` and a
   prompt that focuses on ONE item only: verify claim, verify category,
   surface the authoritative URL.
2. Prefer the URL from ``grounding_metadata.grounding_chunks`` (real
   URL Gemini actually retrieved) — resolve the Vertex redirect once.
3. Apply factual corrections: flag category drift (paper-as-patent),
   note when the claim itself is wrong.
4. Re-fetch the new URL + re-classify content fit.
5. Report before/after distribution.

If this improves content-fit, we wire it into ``grounded_search_json``
as the first stage before the 3-tier fallback.
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
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "audits"
IN_PATH = OUT_DIR / "song_han_items.json"
OUT_PATH = OUT_DIR / "song_han_after_confirm.json"
TXT_PATH = OUT_DIR / "song_han_after_confirm.txt"

sys.path.insert(0, str(REPO_ROOT / "backend"))
from google.genai import types  # noqa: E402
from app.services.academic.llm_client import (  # noqa: E402
    genai_client,
    generate_structured,
)

CONFIRM_MODEL = "gemini-3.1-flash-lite-preview"
CLASSIFY_MODEL = "gemini-3-flash-preview"
SCHOLAR_CONTEXT = (
    "Song Han, Associate Professor at MIT EECS (Microsystems Technology "
    "Laboratories / MIT-IBM Watson AI Lab). Research: efficient deep "
    "learning, model compression, TinyML, quantization, GPU/hardware "
    "co-design. Co-founder of DeePhi Tech (acquired by Xilinx) and OmniML."
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_VERTEX = "vertexaisearch.cloud.google.com"


# ═══════════════════════════════════════════════════════════════════════
# Confirmation
# ═══════════════════════════════════════════════════════════════════════

_CONFIRM_PROMPT = (
    "You MUST use Google Search to verify ONE specific item. Do NOT rely "
    "on training-data memory alone — ground every claim in the search "
    "results you retrieve.\n\n"
    "SCHOLAR CONTEXT: {context}\n\n"
    "ITEM UNDER REVIEW\n"
    "  source category: {category}\n"
    "  title / name: {title}\n"
    "  summary / claim: {summary}\n\n"
    "Your job:\n"
    "1. Search for this item. Does it exist roughly as described?\n"
    "2. Is the source category correct?\n"
    "   - news: a news event, press release, or blog post\n"
    "   - patent: a filed patent application or granted patent (NOT a "
    "research paper or a product announcement)\n"
    "   - startup: a real company the scholar founded/co-founded\n"
    "   - red_flag: a documented reputational concern\n"
    "3. Identify the single authoritative URL (the primary/official "
    "source). Prefer the actual event page, patent-office record, "
    "company homepage, or news article — NOT aggregator pages or "
    "generic hubs.\n\n"
    "Return a JSON object only:\n"
    '{{"verdict": "confirmed" | "partial" | "unconfirmed",\n'
    '  "category_correct": true | false,\n'
    '  "correction_note": "<if category wrong or facts are off, explain '
    'briefly; else empty>",\n'
    '  "authoritative_url": "<best URL from your search, or empty>",\n'
    '  "evidence": "<one sentence summary of what you found>"}}\n'
)


class _Confirmation(BaseModel):
    verdict: str
    category_correct: bool = True
    correction_note: str = ""
    authoritative_url: str = ""
    evidence: str = ""


async def _confirm(
    item: dict[str, Any], context: str,
) -> dict[str, Any]:
    """Run one grounded-search confirmation for one item. Returns a
    dict with verdict + authoritative URL + grounding chunks."""
    client = genai_client()
    prompt = _CONFIRM_PROMPT.format(
        context=context,
        category=item.get("source", ""),
        title=(item.get("title") or "")[:300],
        summary=(item.get("summary") or "")[:400],
    )
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    try:
        resp = await client.aio.models.generate_content(
            model=CONFIRM_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "error",
            "authoritative_url": "",
            "category_correct": True,
            "correction_note": f"confirm_error: {exc}",
            "evidence": "",
            "grounding_urls": [],
        }

    text = resp.text or ""
    # JSON parse
    parsed: dict[str, Any] = {}
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Grounding chunks = real URLs Gemini retrieved for this item
    grounding_urls: list[str] = []
    cands = getattr(resp, "candidates", None) or []
    if cands:
        gm = getattr(cands[0], "grounding_metadata", None)
        for ch in getattr(gm, "grounding_chunks", None) or []:
            w = getattr(ch, "web", None)
            if w and getattr(w, "uri", ""):
                grounding_urls.append(w.uri)

    return {
        "verdict": (parsed.get("verdict") or "").lower().strip() or "unconfirmed",
        "authoritative_url": (parsed.get("authoritative_url") or "").strip(),
        "category_correct": bool(parsed.get("category_correct", True)),
        "correction_note": (parsed.get("correction_note") or "").strip(),
        "evidence": (parsed.get("evidence") or "").strip(),
        "grounding_urls": grounding_urls,
    }


async def _resolve_redirect(client: httpx.AsyncClient, url: str) -> str:
    """Follow a Vertex grounding-redirect once to get the canonical URL."""
    if not url or _VERTEX not in url:
        return url
    try:
        resp = await client.head(
            url, follow_redirects=True,
            headers={"User-Agent": UA},
        )
        final = str(resp.url)
        if _VERTEX in final:
            resp = await client.get(
                url, follow_redirects=True,
                headers={"User-Agent": UA, "Range": "bytes=0-0"},
            )
            final = str(resp.url)
        if final and _VERTEX not in final:
            return final
    except Exception:
        pass
    return url


async def _apply_confirmation(
    item: dict[str, Any], conf: dict[str, Any],
    client: httpx.AsyncClient,
) -> None:
    """Mutate item in-place with confirmation results."""
    # Decide which URL to attach, preferring grounding chunks over the
    # model's free-text authoritative_url (the former is a real URL
    # Google Search returned; the latter can still be LLM-fabricated).
    new_url = ""
    url_source = "unconfirmed"
    if conf["grounding_urls"]:
        new_url = await _resolve_redirect(client, conf["grounding_urls"][0])
        url_source = "confirmed_grounding"
    elif conf["authoritative_url"].startswith(("http://", "https://")):
        new_url = conf["authoritative_url"]
        url_source = "confirmed_claim"

    item["previous_url"] = item.get("url", "")
    if new_url:
        item["url"] = new_url
        item["_url_source"] = url_source
    else:
        item["_url_source"] = "unconfirmed"
    item["_confirmation_verdict"] = conf["verdict"]
    item["_category_correct"] = conf["category_correct"]
    if not conf["category_correct"]:
        item["_invalid_claim"] = conf["correction_note"] or "category mismatch"
    if conf["correction_note"]:
        item["_correction_note"] = conf["correction_note"]
    item["_confirmation_evidence"] = conf["evidence"]


# ═══════════════════════════════════════════════════════════════════════
# Re-fetch + content-fit classifier (reuse logic from earlier audit)
# ═══════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(h: str, limit: int = 2500) -> str:
    h = re.sub(r"<script[\s\S]*?</script>", " ", h, flags=re.IGNORECASE)
    h = re.sub(r"<style[\s\S]*?</style>", " ", h, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", h)
    return _WS_RE.sub(" ", html.unescape(text)).strip()[:limit]


def _extract_title(h: str) -> str:
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", h, flags=re.IGNORECASE)
    if not m:
        return ""
    return _WS_RE.sub(" ", html.unescape(m.group(1))).strip()[:300]


async def _fetch(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    if not url:
        return {"status": None, "final_url": "", "page_title": "", "snippet": ""}
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
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "final_url": "",
                "page_title": "", "snippet": f"fetch_error: {exc}"}


class _FitLabel(BaseModel):
    classification: str
    reason: str


_FIT_PROMPT = (
    "Validate whether a URL leads to content about a specific item.\n\n"
    "ITEM\n  source: {source}\n  title: {title}\n  summary: {summary}\n\n"
    "FETCHED PAGE\n  url: {url}\n  final_url: {final_url}\n"
    "  page_title: {page_title}\n  snippet: {snippet}\n\n"
    "Classify as ONE keyword:\n"
    "- relevant: page is about this specific item\n"
    "- tangential: mentions the scholar/topic but not the primary "
    "subject\n"
    "- generic_homepage: homepage or section hub, no specific coverage\n"
    "- wrong_topic: entirely unrelated\n"
    "- search_no_results: search page with no relevant hits\n"
    "- search_with_hits: search page with plausibly relevant hits\n"
    "- blocked: captcha, login wall, bot block\n"
    "- unclear: insufficient evidence\n\n"
    "Return {{\"classification\": \"<keyword>\", \"reason\": \"<one "
    "sentence>\"}}."
)


async def _classify(item: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    prompt = _FIT_PROMPT.format(
        source=item.get("source", ""),
        title=(item.get("title") or "")[:200],
        summary=(item.get("summary") or "")[:400],
        url=(item.get("url") or "")[:250],
        final_url=(page.get("final_url") or "")[:250],
        page_title=(page.get("page_title") or "")[:200],
        snippet=(page.get("snippet") or "")[:2000],
    )
    try:
        label = await generate_structured(
            model=CLASSIFY_MODEL,
            prompt_parts=[prompt],
            response_schema=_FitLabel,
        )
        return {"classification": label.classification,
                "reason": label.reason}
    except Exception as exc:  # noqa: BLE001
        return {"classification": "classifier_error",
                "reason": f"{type(exc).__name__}: {exc}"}


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════


async def main() -> int:
    items = json.loads(IN_PATH.read_text(encoding="utf-8"))
    # Keep items with a URL present (confirmation needs title/summary anyway
    # but we compare old vs new URL).
    items = [it for it in items if it.get("title")]
    print(f"Prototype: confirming {len(items)} items via {CONFIRM_MODEL}",
          file=sys.stderr)

    sem_confirm = asyncio.Semaphore(5)

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=8.0)) as client:

        async def _one_confirm(it):
            async with sem_confirm:
                conf = await _confirm(it, SCHOLAR_CONTEXT)
                await _apply_confirmation(it, conf, client)
                return it

        t0 = time.monotonic()
        items = list(await asyncio.gather(*(_one_confirm(it) for it in items)))
        print(f"  confirm wall time: {time.monotonic() - t0:.1f}s",
              file=sys.stderr)

        # Re-fetch new URLs and classify content fit
        print(f"Fetching + classifying {len(items)} new URLs", file=sys.stderr)
        t1 = time.monotonic()
        pages = await asyncio.gather(*(_fetch(client, it.get("url", "")) for it in items))
        sem_cls = asyncio.Semaphore(6)

        async def _one_cls(it, p):
            async with sem_cls:
                return await _classify(it, p)

        labels = await asyncio.gather(
            *(_one_cls(it, p) for it, p in zip(items, pages))
        )
        print(f"  fetch+classify wall time: {time.monotonic() - t1:.1f}s",
              file=sys.stderr)

    # Assemble output
    results = []
    for it, page, lbl in zip(items, pages, labels):
        results.append({
            **it,
            "fetched_status": page.get("status"),
            "fetched_final_url": page.get("final_url"),
            "fetched_page_title": page.get("page_title"),
            "new_classification": lbl["classification"],
            "new_classification_reason": lbl["reason"],
        })

    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # ── Summary ──
    lines: list[str] = []
    lines.append(f"Per-item confirmation prototype — {len(results)} items\n")

    # Load the BEFORE classifications from content-fit audit if available
    fit_path = OUT_DIR / "song_han_content_fit.json"
    before: dict[str, str] = {}
    if fit_path.exists():
        for r in json.loads(fit_path.read_text(encoding="utf-8")):
            key = r.get("title") + "|" + r.get("source", "")
            before[key] = r.get("classification", "")

    after_ctr = Counter(r["new_classification"] for r in results)
    lines.append("AFTER confirmation — classification distribution:")
    total = len(results)
    for cls, n in after_ctr.most_common():
        lines.append(f"  {cls:<22} {n:>3}  ({n / total * 100:5.1f}%)")

    # Comparison with before
    if before:
        before_ctr = Counter(before.values())
        lines.append("")
        lines.append("BEFORE (content-fit audit):")
        for cls, n in before_ctr.most_common():
            lines.append(f"  {cls:<22} {n:>3}")
        lines.append("")
        lines.append("DELTA:")
        for cls in set(after_ctr) | set(before_ctr):
            before_n = before_ctr.get(cls, 0)
            after_n = after_ctr.get(cls, 0)
            diff = after_n - before_n
            sign = "+" if diff > 0 else ""
            lines.append(
                f"  {cls:<22} before={before_n:>3}  after={after_n:>3}  "
                f"({sign}{diff})"
            )

    # Corrections
    invalid = [r for r in results if r.get("_invalid_claim")]
    lines.append("")
    lines.append(f"Category / factual corrections ({len(invalid)}):")
    for r in invalid:
        lines.append(f"  [{r['source']}] {r['title'][:80]}")
        lines.append(f"     note: {r.get('_invalid_claim', '')[:200]}")

    # URL changes
    lines.append("")
    lines.append("=== PER-ITEM BEFORE/AFTER ===")
    for r in results:
        prev_url = r.get("previous_url") or ""
        new_url = r.get("url") or ""
        changed = prev_url != new_url
        lines.append(
            f"\n[{r['source']}/{r.get('_url_source')}] "
            f"fit={r['new_classification']} "
            f"verdict={r.get('_confirmation_verdict')}"
        )
        lines.append(f"  title  : {r['title'][:100]}")
        lines.append(f"  before : {prev_url[:120]}")
        lines.append(f"  after  : {new_url[:120]} {'(changed)' if changed else ''}")
        if r.get("_invalid_claim"):
            lines.append(f"  INVALID CLAIM: {r['_invalid_claim'][:200]}")
        lines.append(f"  fit reason: {r['new_classification_reason'][:200]}")

    summary = "\n".join(lines)
    TXT_PATH.write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
