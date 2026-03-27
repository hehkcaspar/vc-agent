"""Normalize Gemini JSON for file lookup / metadata pre-process (file_lookup_preprocess.md)."""

from __future__ import annotations

from typing import Any, Dict

_IMAGE_TREATMENTS = frozenset({"not_image", "ocr", "visual_description"})


def _normalize_image_content(raw: Any) -> Dict[str, Any]:
    """OCR vs objective visual description for raster pre-process."""
    if not isinstance(raw, dict):
        return {
            "treatment": "not_image",
            "ocr_text": None,
            "objective_visual_description": None,
        }
    tr = raw.get("treatment")
    if tr not in _IMAGE_TREATMENTS:
        tr = "not_image"

    ocr = raw.get("ocr_text")
    ocr_s = ocr.strip() if isinstance(ocr, str) and ocr.strip() else None

    desc = raw.get("objective_visual_description")
    if not isinstance(desc, str) or not desc.strip():
        alt = raw.get("objective_image_description")
        desc = alt if isinstance(alt, str) else desc
    desc_s = desc.strip() if isinstance(desc, str) and desc.strip() else None

    if tr == "ocr" and not ocr_s:
        tr = "not_image"
    if tr == "visual_description" and not desc_s:
        tr = "not_image"
    if tr == "not_image":
        ocr_s = None
        desc_s = None
    elif tr == "ocr":
        desc_s = None
    elif tr == "visual_description":
        ocr_s = None

    return {
        "treatment": tr,
        "ocr_text": ocr_s,
        "objective_visual_description": desc_s,
    }


def normalize_file_lookup_result(result: Any) -> Dict[str, Any]:
    """Normalize model output to stable file-lookup index + image_content."""
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0]
    if not isinstance(result, dict):
        return _default_file_lookup_result()

    def _rel(x: Any) -> str:
        if x in ("high", "medium", "low"):
            return str(x)
        return "low"

    def _len_sig(x: Any) -> str:
        if x in (
            "very_short",
            "short",
            "medium",
            "long",
            "unknown",
        ):
            return str(x)
        return "unknown"

    def _kind(x: Any) -> str:
        allowed = {
            "pitch_deck",
            "financial_statement",
            "legal",
            "memo",
            "email_or_letter",
            "spreadsheet_data",
            "presentation",
            "research",
            "press_or_news",
            "code_or_config",
            "image_or_scan",
            "mixed",
            "other",
            "unknown",
        }
        if isinstance(x, str) and x in allowed:
            return x
        return "unknown"

    ftr = result.get("full_text_recommended")
    if not isinstance(ftr, dict):
        ftr = {}
    reason = ftr.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = (
            "Unable to confirm from excerpt alone; read the full file when precision matters."
        )
    val = ftr.get("value")
    if not isinstance(val, bool):
        val = True

    one = result.get("one_liner")
    if not isinstance(one, str):
        one = ""
    summ = result.get("summary")
    if not isinstance(summ, str):
        summ = ""

    topics = result.get("primary_topics")
    if not isinstance(topics, list):
        topics = []
    topics = [str(t) for t in topics if isinstance(t, (str, int, float)) and str(t).strip()]

    entities = result.get("key_entities_or_parties")
    if not isinstance(entities, list):
        entities = []
    entities = [
        str(e) for e in entities if isinstance(e, (str, int, float)) and str(e).strip()
    ]

    caveats = result.get("caveats")
    if not isinstance(caveats, list):
        caveats = []
    caveats = [
        str(c) for c in caveats if isinstance(c, (str, int, float)) and str(c).strip()
    ]

    langs_raw = result.get("languages")
    languages: list[str] = []
    if isinstance(langs_raw, list):
        languages = [
            str(x).strip()
            for x in langs_raw
            if isinstance(x, (str, int, float)) and str(x).strip()
        ]
    elif isinstance(langs_raw, str) and langs_raw.strip():
        languages = [langs_raw.strip()]
    else:
        legacy = result.get("language")
        if isinstance(legacy, str) and legacy.strip():
            languages = [legacy.strip()]
        elif isinstance(legacy, list):
            languages = [
                str(x).strip()
                for x in legacy
                if isinstance(x, (str, int, float)) and str(x).strip()
            ]

    return {
        "one_liner": one.strip(),
        "summary": summ.strip(),
        "languages": languages,
        "document_kind": _kind(result.get("document_kind")),
        "primary_topics": topics,
        "key_entities_or_parties": entities,
        "approx_length_signal": _len_sig(result.get("approx_length_signal")),
        "full_text_recommended": {"value": val, "reason": reason.strip()},
        "skim_metadata_reliability": _rel(result.get("skim_metadata_reliability")),
        "caveats": caveats,
        "image_content": _normalize_image_content(result.get("image_content")),
    }


def _default_file_lookup_result() -> Dict[str, Any]:
    return normalize_file_lookup_result({})
