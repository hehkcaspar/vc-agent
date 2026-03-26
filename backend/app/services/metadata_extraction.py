"""Normalize Gemini JSON extraction output to a stable VC metadata shape."""

from __future__ import annotations

from typing import Any, Dict


def normalize_extraction_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0]
    if not isinstance(result, dict):
        return _default_extraction_result()

    return {
        "company_name": result.get("company_name")
        or {"value": None, "confidence": "low"},
        "founders": result.get("founders") or [],
        "industry_tags": result.get("industry_tags") or [],
        "investment_stage": result.get("investment_stage")
        or {"value": "unknown", "confidence": "low"},
        "company_description": result.get("company_description")
        or {"value": None, "confidence": "low"},
        "company_website": result.get("company_website"),
        "funding_ask": result.get("funding_ask"),
        "referral_source": result.get("referral_source"),
        "priority_indicators": result.get("priority_indicators") or [],
        "red_flags": result.get("red_flags") or [],
        "competitors_mentioned": result.get("competitors_mentioned") or [],
    }


def _default_extraction_result() -> Dict[str, Any]:
    return {
        "company_name": {"value": None, "confidence": "low"},
        "founders": [],
        "industry_tags": [],
        "investment_stage": {"value": "unknown", "confidence": "low"},
        "company_description": {"value": None, "confidence": "low"},
        "company_website": None,
        "funding_ask": None,
        "referral_source": None,
        "priority_indicators": [],
        "red_flags": [],
        "competitors_mentioned": [],
    }
