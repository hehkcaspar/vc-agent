"""Extract-info signals splitter + formatter.

The ``extract_info`` agent emits a combined payload today (Tier 1-3 facts +
signals). Post-processing splits off the signals block (``priority_indicators``,
``red_flags``, ``competitors``) and writes it to
``Deliverables/Analysis/extract_info_signals.json`` — keeping
``Company Profile.json`` and ``Entity.metadata_json`` pure facts.

See docs/design/FACTS_VS_OPINIONS.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

SIGNAL_KEYS = ("priority_indicators", "red_flags", "competitors")

SIGNALS_WORKSPACE_PATH = "Deliverables/Analysis/extract_info_signals.json"


def split_extract_info_payload(
    payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Split the agent's combined extract_info output into ``(facts, signals)``.

    Mutates neither input. Returns:

    - ``facts`` — a shallow copy of the payload with signal keys removed
    - ``signals`` — ``{priority_indicators: [...], red_flags: [...], competitors: [...]}``;
      keys are always present, empty list when absent from the payload
    """
    if not isinstance(payload, dict):
        raise ValueError(f"Expected dict, got {type(payload).__name__}")

    signals: Dict[str, List[str]] = {k: [] for k in SIGNAL_KEYS}
    facts: Dict[str, Any] = {}

    for key, value in payload.items():
        if key in SIGNAL_KEYS:
            # Normalise: accept either list[str] or list[dict{text}] or None.
            signals[key] = _normalise_signal_array(value)
        else:
            facts[key] = value

    return facts, signals


def _normalise_signal_array(value: Any) -> List[str]:
    """Accept the agent's looser shapes and return a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                out.append(item.strip())
        elif isinstance(item, dict):
            text = item.get("text") or item.get("description") or item.get("value")
            if isinstance(text, str) and text.strip():
                out.append(text.strip())
    return out


def build_signals_document(
    signals: Dict[str, List[str]],
    *,
    run_id: str | None,
    files_examined: List[str] | None,
) -> Dict[str, Any]:
    """Wrap the signals payload with run metadata for persistence."""
    return {
        **{k: signals.get(k) or [] for k in SIGNAL_KEYS},
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "_generated_by_run_id": run_id,
        "_files_examined": list(files_examined or []),
    }


def has_any_signal(signals: Dict[str, List[str]] | None) -> bool:
    """Returns True when at least one signal array is non-empty."""
    if not signals:
        return False
    return any((signals.get(k) or []) for k in SIGNAL_KEYS)
