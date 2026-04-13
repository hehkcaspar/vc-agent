"""Layer 2 source — red flag detection via Gemini grounded search.

Concept 7 from the framework. Uses Path 2 (grounded search) to scan
Retraction Watch, PubPeer, news outlets, and general web for
retractions, misconduct findings, lawsuits, failed ventures, and
ethics concerns affecting the scholar. New findings are appended as
`flag` events to `red_flags.jsonl`.

Existing active flags (from `fact_store.active_red_flags`) are passed
into the prompt so the model avoids duplicates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..fact_store import active_red_flags, record_snapshot
from ..file_utils import append_record, dossier_path, read_json

logger = logging.getLogger(__name__)

# Codes that mean the page actually exists (even if rendered client-side).
_OK_CODES = set(range(200, 400)) | {202, 405}  # 405 = method not allowed (HEAD blocked but page exists)


async def _check_url(url: str) -> bool:
    """Return True if the URL responds with a non-error status."""
    if not url or not url.startswith("http"):
        return False
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(10),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            r = await client.head(url)
            if r.status_code in _OK_CODES:
                return True
            # Some sites block HEAD — retry with GET
            r = await client.get(url, headers={"Range": "bytes=0-0"})
            return r.status_code in _OK_CODES
    except Exception:
        return False


async def _fix_url(claim: str, broken_url: str, scholar_name: str) -> str:
    """Ask Gemini grounded search to find the correct source URL for a claim."""
    try:
        from ..llm_client import grounded_generate_text
        text = await grounded_generate_text([
            f"Find the real, working URL for this claim about {scholar_name}: "
            f'"{claim}"\n\n'
            f"The original URL was {broken_url} but it returns a 404 error. "
            f"Search the web and return ONLY the correct URL, nothing else. "
            f"If you cannot find a valid source, return the word NONE."
        ])
        text = text.strip()
        if text.upper() == "NONE" or not text.startswith("http"):
            # Try to extract a URL from the response
            import re
            m = re.search(r"https?://[^\s<>\"']+", text)
            if m:
                return m.group(0).rstrip(".,;)")
            return ""
        return text.split()[0].rstrip(".,;)")  # first URL token
    except Exception:
        logger.debug("_fix_url failed for %s", broken_url, exc_info=True)
        return ""


async def _validate_and_fix_urls(
    findings: list[dict], scholar_name: str
) -> list[dict]:
    """Validate source_url for each finding; fix broken ones via Gemini."""

    async def _process(f: dict) -> dict:
        url = (f.get("source_url") or "").strip()
        if not url:
            return f
        if await _check_url(url):
            return f
        logger.info("red_flags_watch: broken URL %s — asking Gemini for fix", url)
        fixed = await _fix_url(f.get("claim", ""), url, scholar_name)
        if fixed and fixed != url and await _check_url(fixed):
            logger.info("red_flags_watch: fixed URL %s → %s", url, fixed)
            f["source_url"] = fixed
        else:
            logger.info("red_flags_watch: could not fix URL %s — clearing", url)
            f["source_url"] = ""
            f["source_summary"] = (f.get("source_summary") or "") + " [source URL unavailable]"
        return f

    return list(await asyncio.gather(*(_process(f) for f in findings)))

SOURCE_ID = "red_flags_watch"

_CATEGORIES = [
    "retraction",
    "misconduct",
    "lawsuit",
    "failed_venture",
    "ethics_concern",
    "clawback",
    "sanctions",
    "export_control",
    "political_risk",
]

_PROMPT_TEMPLATE = (
    "You are screening for business-reputation red flags on the academic "
    "researcher **{name}**{affiliation_clause}. Search for any of: "
    "paper retractions (Retraction Watch, journal notices), research "
    "misconduct findings (ORI, PubPeer), active lawsuits, ventures "
    "involving fraud or investor misconduct, ethics concerns, grant "
    "clawbacks, sanctions (OFAC, entity lists), export-control "
    "violations, or political-risk exposure.\n\n"
    "**CRITICAL SEVERITY GUIDANCE — read carefully:**\n\n"
    "A failed startup is NOT automatically a red flag. In venture "
    "capital, a clean business failure (market timing, tech didn't "
    "work, ran out of runway — no fraud, no misconduct, no investor "
    "lawsuit) is a NEUTRAL event, often even a positive signal of "
    "commercial experience. Only flag venture failures when there is "
    "evidence of fraud, fiduciary breach, investor lawsuits, or "
    "clawback.\n\n"
    "Severity calibration:\n"
    "- `low` — contextual note, no material concern (clean venture "
    "shutdown, personnel departure, vague political-risk, dual-use "
    "concern without sanctions)\n"
    "- `medium` — worth monitoring but bounded (venture failure with "
    "meaningful investor losses but no fraud; export-control "
    "concerns without active enforcement)\n"
    "- `high` — material concern requiring investigation (active "
    "litigation, ethics investigation underway, grant clawback, "
    "partial retraction)\n"
    "- `critical` — hard negative (confirmed fraud, confirmed "
    "research misconduct finding by ORI or equivalent, OFAC "
    "sanctions, full paper retraction with evidence of fabrication)\n\n"
    "Do NOT assign `high` or `critical` to a venture that simply "
    "failed or shut down. That is `low` unless fraud/misconduct is "
    "proven.\n\n"
    "Already-known flags (do NOT repeat these):\n{known}\n\n"
    "For each NEW finding return a JSON object with: "
    "`category` (one of: {categories}), "
    "`severity` (one of: low, medium, high, critical), "
    "`claim` (1-2 sentences), `source_url`, `source_summary`, "
    "`affected_dimensions` (subset of: academic_excellence, "
    "tech_transfer_experience, founder_potential, growth_trajectory). "
    "Return a JSON array; empty if no new findings."
)


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    profile = read_json(dossier_path(scholar_id) / "profile.json") or {}
    name = profile.get("name")
    if not name:
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_scholar_name"},
        )
        return {"changed": False, "snapshot_id": sid, "error": "no_scholar_name"}

    affiliation = ((profile.get("affiliation") or {}).get("current")) or ""
    affiliation_clause = f" at {affiliation}" if affiliation else ""

    existing = active_red_flags(scholar_id)
    known_lines = (
        "\n".join(
            f"- {f.get('category')}: {f.get('claim') or ''}" for f in existing
        )
        or "(none)"
    )
    prompt = _PROMPT_TEMPLATE.format(
        name=name,
        affiliation_clause=affiliation_clause,
        known=known_lines,
        categories=", ".join(_CATEGORIES),
    )

    try:
        from ..llm_client import grounded_search_json  # type: ignore
    except ImportError:
        logger.info("red_flags_watch: llm_client not yet available; skipping")
        snapshot_id = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "skipped": "no_llm_client"}
        )
        return {"changed": False, "snapshot_id": snapshot_id, "skipped": True}

    try:
        findings = await grounded_search_json(prompt)
    except Exception as e:
        logger.exception("red_flags_watch: grounded search failed for %s", scholar_id)
        sid = await record_snapshot(
            scholar_id, SOURCE_ID, detail={"mode": mode, "error": str(e)},
        )
        return {"changed": False, "snapshot_id": sid, "error": str(e)}

    if not isinstance(findings, list):
        findings = []

    # Validate source URLs — fix broken ones via a follow-up Gemini
    # grounded search, or clear them if unfixable.
    if findings:
        findings = await _validate_and_fix_urls(findings, name)

    # Validate each finding: must be a dict, must have a known
    # category, valid severity, and a non-empty claim. Reject garbage
    # so red_flags.jsonl stays clean.
    _VALID_SEV = {"low", "medium", "high", "critical"}
    count = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        if f.get("category") not in _CATEGORIES:
            continue
        if f.get("severity") not in _VALID_SEV:
            continue
        if not (f.get("claim") or "").strip():
            continue
        # Normalize affected_dimensions to a list of known dim ids.
        dims = f.get("affected_dimensions")
        if not isinstance(dims, list):
            dims = []
        f["affected_dimensions"] = [
            d for d in dims
            if d in {"academic_excellence", "tech_transfer_experience",
                     "founder_potential", "growth_trajectory"}
        ]
        await append_record(
            scholar_id,
            "red_flags",
            {"type": "flag", **f},
        )
        count += 1

    snapshot_id = await record_snapshot(
        scholar_id,
        SOURCE_ID,
        detail={"mode": mode, "reason": reason, "new_flags": count},
    )
    return {"changed": count > 0, "snapshot_id": snapshot_id, "new_flags": count}
