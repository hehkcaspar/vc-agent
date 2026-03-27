"""Parse JSON from model output (raw, fenced, or prose-wrapped)."""

from __future__ import annotations

import json
import re
from typing import Any


def _dict_from_value(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0]
    return None


def _scan_open_braces(fragment: str) -> dict[str, Any] | None:
    """Decode the first valid top-level JSON object by trying each '{' position."""
    decoder = json.JSONDecoder()
    start = 0
    while True:
        i = fragment.find("{", start)
        if i < 0:
            return None
        try:
            value, _ = decoder.raw_decode(fragment, i)
            out = _dict_from_value(value)
            if out is not None:
                return out
        except json.JSONDecodeError:
            pass
        start = i + 1


def _scan_open_brackets(fragment: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    start = 0
    while True:
        i = fragment.find("[", start)
        if i < 0:
            return None
        try:
            value, _ = decoder.raw_decode(fragment, i)
            out = _dict_from_value(value)
            if out is not None:
                return out
        except json.JSONDecodeError:
            pass
        start = i + 1


def parse_json_loose(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if not s:
        raise json.JSONDecodeError("empty response", s, 0)

    got = _scan_open_braces(s)
    if got is not None:
        return got
    got = _scan_open_brackets(s)
    if got is not None:
        return got

    fenced = re.search(
        r"```(?:json)?\s*([\s\S]*?)\s*```",
        s,
        re.IGNORECASE,
    )
    if fenced:
        inner = fenced.group(1).strip()
        got = _scan_open_braces(inner)
        if got is not None:
            return got
        got = _scan_open_brackets(inner)
        if got is not None:
            return got

    raise json.JSONDecodeError("no JSON object found", s, 0)
