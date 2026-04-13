"""Minimal public-ORCID fetcher for identity verification.

Used exclusively by the identity verifier to fetch a light
"fingerprint" of an ORCID profile (name, bio, employment, recent
work titles) so the LLM can decide whether the ORCID id belongs to
the scholar under question. Not meant to be a general-purpose ORCID
client — if you need works data for Layer 2 fetchers, build a
proper source module.

Public ORCID API (`pub.orcid.org`) requires no auth and has generous
rate limits. We ask for JSON via the `Accept` header.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_ORCID_BASE = "https://pub.orcid.org/v3.0"
_HEADERS = {"Accept": "application/json"}
_TIMEOUT = 15.0


async def fetch_orcid_fingerprint(orcid_id: str) -> dict[str, Any]:
    """Return `{name, biography, employments, work_titles}` for an ORCID id.

    All fields are best-effort: ORCID profiles vary widely in how
    complete they are. Returns an empty dict on any HTTP/parse error
    so the caller can fall through to a "low evidence" verdict.
    """
    if not orcid_id:
        return {}

    out: dict[str, Any] = {"orcid_id": orcid_id}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            person = await _get(client, f"{_ORCID_BASE}/{orcid_id}/person")
            works = await _get(client, f"{_ORCID_BASE}/{orcid_id}/works")
            employments = await _get(
                client, f"{_ORCID_BASE}/{orcid_id}/employments"
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("orcid_client: fetch failed for %s: %s", orcid_id, e)
        return out

    out["name"] = _extract_name(person)
    out["biography"] = _extract_biography(person)
    out["employments"] = _extract_employments(employments)
    out["work_titles"] = _extract_work_titles(works)
    return out


async def _get(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    resp = await client.get(url)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json() or {}


def _extract_name(person: dict[str, Any]) -> str | None:
    name = (person.get("name") or {})
    given = ((name.get("given-names") or {}).get("value")) or ""
    family = ((name.get("family-name") or {}).get("value")) or ""
    full = f"{given} {family}".strip()
    return full or None


def _extract_biography(person: dict[str, Any]) -> str | None:
    bio = person.get("biography") or {}
    text = bio.get("content") if isinstance(bio, dict) else None
    if not text:
        return None
    return text[:600]  # LLM doesn't need more than a short paragraph


def _extract_employments(employments: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    affils = employments.get("affiliation-group") or []
    for group in affils:
        for summary_wrap in group.get("summaries") or []:
            summary = summary_wrap.get("employment-summary") or {}
            org = (summary.get("organization") or {}).get("name")
            dept = summary.get("department-name")
            role = summary.get("role-title")
            start = _yearify(summary.get("start-date"))
            end = _yearify(summary.get("end-date"))
            if org or role:
                out.append(
                    {
                        "organization": org,
                        "department": dept,
                        "role": role,
                        "start_year": start,
                        "end_year": end,
                    }
                )
    # Most recent first
    out.sort(key=lambda e: (e.get("end_year") or 9999), reverse=True)
    return out[:8]


def _extract_work_titles(works: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for group in (works.get("group") or [])[:20]:
        for summary in group.get("work-summary") or []:
            title_wrap = summary.get("title") or {}
            title = (title_wrap.get("title") or {}).get("value")
            if title:
                titles.append(title)
                break
    return titles[:10]


def _yearify(date_obj: Any) -> int | None:
    if not date_obj:
        return None
    year = (date_obj.get("year") or {}).get("value") if isinstance(date_obj, dict) else None
    try:
        return int(year) if year else None
    except (TypeError, ValueError):
        return None
