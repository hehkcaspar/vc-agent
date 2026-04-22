"""Diagnose each non-relevant Song Han item: URL-hallucination or
claim-hallucination?

For every item classified `wrong_topic` or `tangential` in
``data/audits/song_han_content_fit.json``:

1. Ask gemini-3.1-flash-lite-preview with the google_search tool to
   independently verify whether the claim / patent / company / news
   event is real. Return {verdict, corroborating_url, evidence}.
2. Also inspect the response's ``grounding_metadata.grounding_chunks``
   — those are the actual URLs Google Search returned. Strong signal
   of whether any real source exists.
3. Classify the root cause:
   - **url_hallucination**: claim looks real (verdict=confirmed OR
     model found corroborating chunks) but our stored URL was wrong.
   - **claim_hallucination**: claim looks fake (verdict=unconfirmed
     AND no supportive grounding chunks).
   - **partial**: verdict=partial.

This is purely diagnostic; no production code is touched.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "audits"
IN_PATH = OUT_DIR / "song_han_content_fit.json"
OUT_PATH = OUT_DIR / "song_han_halluc_roots.json"
TXT_PATH = OUT_DIR / "song_han_halluc_roots.txt"

sys.path.insert(0, str(REPO_ROOT / "backend"))

from google.genai import types  # noqa: E402
from app.services.academic.llm_client import genai_client  # noqa: E402

MODEL = "gemini-3.1-flash-lite-preview"


class Verdict(BaseModel):
    verdict: str  # confirmed | unconfirmed | partial
    corroborating_url: str = ""
    evidence: str = ""


_PROMPT = (
    "Use Google Search to independently verify whether the item below "
    "is a real, documented thing. Do NOT rely on memory — you must "
    "search. Context: the scholar is Song Han, associate professor at "
    "MIT EECS, researcher in efficient deep learning / TinyML / "
    "quantization / GPU systems.\n\n"
    "ITEM\n"
    "  source category: {source}\n"
    "  title / name: {title}\n"
    "  summary: {summary}\n\n"
    "Decide:\n"
    '- "confirmed": search returns a primary source (news article, '
    "patent filing, company website, journal page) that matches the "
    "item's specific claim\n"
    '- "partial": the general topic exists but a specific detail in the '
    "item (patent number, exact company name, specific event, etc.) "
    "does not line up with anything in the search results\n"
    '- "unconfirmed": search returns nothing that supports the item as '
    "described\n\n"
    "Return ONLY a JSON object: "
    '{{"verdict": "...", "corroborating_url": "...", '
    '"evidence": "<one sentence citing what search found>"}}.'
)


async def _verify(item: dict[str, Any]) -> dict[str, Any]:
    client = genai_client()
    prompt = _PROMPT.format(
        source=item.get("source", ""),
        title=(item.get("title") or "")[:300],
        summary=(item.get("summary") or "")[:400],
    )
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    resp = await client.aio.models.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=config,
    )
    text = resp.text or ""

    # Parse the JSON object out of the text (grounded calls can't use
    # response_schema directly alongside tools).
    import re
    m = re.search(r"\{[\s\S]*\}", text)
    verdict_obj: dict[str, Any] = {}
    if m:
        try:
            verdict_obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Pull grounding chunks — real URLs Gemini actually retrieved.
    chunks: list[dict[str, Any]] = []
    cands = getattr(resp, "candidates", None) or []
    if cands:
        gm = getattr(cands[0], "grounding_metadata", None)
        for ch in getattr(gm, "grounding_chunks", None) or []:
            w = getattr(ch, "web", None)
            if not w:
                continue
            chunks.append({
                "uri": getattr(w, "uri", "") or "",
                "title": getattr(w, "title", "") or "",
                "domain": getattr(w, "domain", "") or "",
            })
    web_queries = (
        getattr(gm, "web_search_queries", None) if cands and gm else None
    ) or []

    verdict = (verdict_obj.get("verdict") or "").strip().lower()
    corroborating_url = (verdict_obj.get("corroborating_url") or "").strip()
    evidence = (verdict_obj.get("evidence") or "").strip()

    return {
        "raw_text": text[:400],
        "verdict": verdict,
        "corroborating_url": corroborating_url,
        "evidence": evidence,
        "grounding_chunk_domains": [c["domain"] or c["title"] for c in chunks][:6],
        "grounding_chunk_count": len(chunks),
        "web_queries": web_queries[:4],
    }


def _classify_root(diag: dict[str, Any]) -> str:
    v = diag["verdict"]
    has_chunks = diag["grounding_chunk_count"] > 0
    if v == "confirmed":
        return "url_hallucination"
    if v == "partial":
        return "partial_hallucination"
    if v == "unconfirmed" and not has_chunks:
        return "claim_hallucination"
    if v == "unconfirmed" and has_chunks:
        # Model said "no" but search did find something — possible
        # false-negative from a weak model; flag for manual review.
        return "claim_hallucination_search_saw_chunks"
    return "indeterminate"


async def main() -> int:
    data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    suspect = [
        r for r in data
        if r.get("classification") in ("wrong_topic", "tangential")
    ]
    print(f"Verifying {len(suspect)} non-relevant items via {MODEL}",
          file=sys.stderr)

    sem = asyncio.Semaphore(4)

    async def _one(item):
        async with sem:
            try:
                return await _verify(item)
            except Exception as exc:  # noqa: BLE001
                return {
                    "raw_text": "",
                    "verdict": "error",
                    "corroborating_url": "",
                    "evidence": f"{type(exc).__name__}: {exc}",
                    "grounding_chunk_domains": [],
                    "grounding_chunk_count": 0,
                    "web_queries": [],
                }

    diags = await asyncio.gather(*(_one(it) for it in suspect))

    out: list[dict[str, Any]] = []
    for item, diag in zip(suspect, diags):
        root = _classify_root(diag)
        out.append({
            "source": item["source"],
            "tier": item["tier"],
            "original_classification": item["classification"],
            "title": item["title"],
            "summary": item["summary"],
            "stored_url": item["url"],
            "root_cause": root,
            **diag,
        })

    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Summary
    from collections import Counter
    lines: list[str] = []
    lines.append(f"Root-cause diagnosis — {len(out)} non-relevant items\n")
    ctr = Counter(r["root_cause"] for r in out)
    for root, n in ctr.most_common():
        pct = n / len(out) * 100
        lines.append(f"  {root:<40} {n:>3}  ({pct:5.1f}%)")

    lines.append("\nBy source:")
    src_bucket: dict[str, Counter] = {}
    for r in out:
        src_bucket.setdefault(r["source"], Counter())[r["root_cause"]] += 1
    for s, c in sorted(src_bucket.items()):
        lines.append(f"  {s}:")
        for root, n in c.most_common():
            lines.append(f"    {root:<40} {n}")

    lines.append("\n=== PER-ITEM DIAGNOSIS ===")
    for r in out:
        lines.append(
            f"\n[{r['source']}/{r['tier']}] root={r['root_cause']} "
            f"verdict={r['verdict']}"
        )
        lines.append(f"  title    : {r['title'][:120]}")
        lines.append(f"  stored   : {r['stored_url'][:120]}")
        if r.get("corroborating_url"):
            lines.append(f"  REAL url : {r['corroborating_url'][:120]}")
        if r.get("grounding_chunk_domains"):
            lines.append(
                f"  chunks   : {r['grounding_chunk_count']} "
                f"({', '.join(r['grounding_chunk_domains'])})"
            )
        lines.append(f"  evidence : {r['evidence'][:240]}")
        if r.get("web_queries"):
            lines.append(f"  queries  : {r['web_queries']}")

    summary = "\n".join(lines)
    TXT_PATH.write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
