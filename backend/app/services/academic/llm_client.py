"""Thin async wrapper around `google-genai` for scholar tracking.

Self-contained — do NOT import from `app.services.direct_llm` or any
other module outside `services/academic/`. Reusing the retry idiom is
fine (copied, not imported).

Three paths map to three helpers:
- `generate_structured(...)`     — Path 1 (single-shot + response_schema)
- `grounded_search_json(...)`    — Path 2 (grounded search → JSON array)
- `interact(...)`                — Path 3 (Interactions API)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Sequence, Type, TypeVar
from urllib.parse import quote_plus

from google import genai
from google.genai import types
from pydantic import BaseModel

from ...config import settings

logger = logging.getLogger(__name__)

# Fields where a grounded-search item carries its citation URL. The
# grounded_search_json post-processor rewrites whichever is present.
URL_FIELDS = ("url", "source_url")

# Fields searched (in order) to build a fallback Google search URL
# when neither grounding nor the LLM's URL are usable.
_ANCHOR_FIELDS = ("title", "name", "claim", "headline")

T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


def genai_client() -> genai.Client:
    """Process-wide google-genai client singleton."""
    global _client
    if _client is None:
        key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY / GOOGLE_API_KEY not configured"
            )
        _client = genai.Client(api_key=key)
    return _client


# ── retry idiom (copy, don't import) ──────────────────────────────────


async def _with_retry(fn, max_attempts: int = 3):
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 1.0 * (2**attempt)
            logger.warning(
                "llm_client: attempt %d failed (%s); sleeping %.1fs",
                attempt + 1,
                e,
                wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError(
        f"llm_client: call failed after {max_attempts} retries: {last_err}"
    )


def _parts(prompt_parts: Sequence[str]) -> list[dict[str, Any]]:
    """Pack a sequence of text strings into a single user Content block."""
    return [
        {
            "role": "user",
            "parts": [{"text": t} for t in prompt_parts if t],
        }
    ]


# ── Path 1 — single-shot with structured output ───────────────────────


async def generate_structured(
    model: str,
    prompt_parts: Sequence[str],
    response_schema: Type[T],
    *,
    tools: list[types.Tool] | None = None,
    system_instruction: str | None = None,
) -> T:
    """Single-shot Gemini call returning a typed Pydantic model instance.

    If `tools` is provided (e.g. grounded search), JSON-mode is disabled
    automatically — Gemini cannot combine tools with `response_mime_type`
    in every SDK version. The caller is responsible for parsing the
    text output in that case via `grounded_search_json`.
    """
    client = genai_client()

    cfg_kwargs: dict[str, Any] = {}
    if system_instruction:
        cfg_kwargs["system_instruction"] = system_instruction
    if tools:
        cfg_kwargs["tools"] = tools
    else:
        cfg_kwargs["response_mime_type"] = "application/json"
        cfg_kwargs["response_schema"] = response_schema

    config = types.GenerateContentConfig(**cfg_kwargs)

    async def _call():
        return await client.aio.models.generate_content(
            model=model,
            contents=_parts(prompt_parts),
            config=config,
        )

    response = await _with_retry(_call)

    if tools:
        # Grounded path — parse the text manually.
        return _parse_json_as(response, response_schema)

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, response_schema):
        return parsed
    # Some SDK versions return a dict in .parsed instead of the model.
    if isinstance(parsed, dict):
        return response_schema.model_validate(parsed)
    # Last-ditch: parse the text.
    return _parse_json_as(response, response_schema)


def _parse_json_as(response: Any, schema: Type[T]) -> T:
    text = _extract_text(response) or ""
    # Strip markdown fences if the model wrapped the JSON.
    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
    if not m:
        raise ValueError(f"llm_client: no JSON found in response: {text[:200]}")
    data = json.loads(m.group(0))
    return schema.model_validate(data)


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None) or ""
    if text.strip():
        return text.strip()
    candidates = getattr(response, "candidates", None) or []
    parts_out: list[str] = []
    for c in candidates:
        content = getattr(c, "content", None)
        if content and getattr(content, "parts", None):
            for p in content.parts:
                if getattr(p, "text", None):
                    parts_out.append(p.text)
    return "\n".join(parts_out).strip()


# ── Path 2 — grounded search returning a JSON array (untyped) ─────────


async def grounded_generate_text(
    prompt_parts: Sequence[str],
    *,
    model: str | None = None,
) -> str:
    """Single-shot grounded Google Search call returning raw text.

    Used by discovery passes (phase_classifier) where the grounded
    output is free-form prose, not JSON. The caller is responsible
    for downstream structuring (e.g. a second `generate_structured`
    synthesis pass).
    """
    client = genai_client()
    model_id = model or settings.ACADEMIC_GEMINI_MODEL
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )

    async def _call():
        return await client.aio.models.generate_content(
            model=model_id,
            contents=_parts(prompt_parts),
            config=config,
        )

    response = await _with_retry(_call)
    return _extract_text(response)


_RETRY_PREAMBLE = (
    "CRITICAL: this is a retry because the previous response either did "
    "not call Google Search at all, or returned items unbacked by any "
    "search result. You MUST execute at least one Google Search query "
    "before answering. If your search surfaces no relevant results, the "
    "ONLY correct response is the empty array `[]` — do not answer from "
    "training data. Do not fabricate items.\n\n"
)


async def _grounded_search_once(
    prompt: str,
    *,
    model_id: str,
    is_retry: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    """Single grounded-search call. Returns (parsed_items, dropped_count,
    chunks_total) where chunks_total is the response-level grounding-chunk
    count (0 means the model bypassed search entirely).

    Drops items whose ``_url_source`` is ``"no_grounding"`` — those have
    no source citation backing them and are structurally untrustable
    for fact-extraction prompts.
    """
    client = genai_client()
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )

    full_prompt = (_RETRY_PREAMBLE + prompt) if is_retry else prompt

    async def _call():
        return await client.aio.models.generate_content(
            model=model_id,
            contents=_parts([full_prompt]),
            config=config,
        )

    response = await _with_retry(_call)
    text = _extract_text(response)
    if not text:
        return [], 0, 0

    parsed, _array_start, item_spans = _parse_json_array_with_spans(text)
    if not parsed:
        return [], 0, 0

    grounding = _extract_grounding(response, response_text=text)
    queries = grounding.get("queries") or []
    chunks_total = len(grounding.get("chunks") or [])
    if queries:
        logger.info(
            "grounded_search_json%s: model ran %d search queries, %d chunks: %s",
            " (RETRY)" if is_retry else "",
            len(queries), chunks_total, queries[:6],
        )
    _attach_grounding_urls(parsed, text, item_spans, grounding)

    before = len(parsed)
    parsed = [
        it for it in parsed
        if not (
            isinstance(it, dict)
            and (it.get("_url_source") == "no_grounding")
        )
    ]
    dropped = before - len(parsed)
    return parsed, dropped, chunks_total


async def grounded_search_json(
    prompt: str,
    *,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Grounded-search single-shot call returning a parsed JSON array.

    Synchronous, fast, no HTTP:

    - Parses the model's JSON output.
    - Attaches a first-pass URL per item from ``grounding_metadata.
      grounding_chunks`` when an overlapping support exists. The URL
      may be a Vertex redirect (``vertexaisearch...``) — it works now
      but expires in ~30 days.
    - For items with NO URL at all (no grounding, no LLM emit),
      seeds a ``google.com/search?q=<title>`` fallback so every item
      is clickable.

    URL *quality* refinement (resolving redirects, validating LLM URLs,
    per-item verification, category triage) is deliberately NOT done
    here. It lives downstream in ``refinement.refine_pending_items``,
    which runs as a background task against persisted records.

    Retry semantics (Tier 3 fix, 2026-05-02): if the first call
    produced 0 grounding chunks (model bypassed Google Search) AND
    every item got dropped as ungrounded, retry once with a
    prepended stricter directive. Empirically, Gemini sometimes
    decides to answer from memory even when ``GoogleSearch`` is
    enabled — the retry catches that without making every call cost
    twice. On retry-still-zero, return ``[]`` (caller observes via
    the absence of items).
    """
    model_id = model or settings.ACADEMIC_GEMINI_MODEL

    parsed, dropped, chunks_total = await _grounded_search_once(
        prompt, model_id=model_id, is_retry=False,
    )

    # Retry condition: model bypassed search entirely (no chunks at all)
    # AND every item the model emitted got dropped. Don't retry when we
    # have at least one survivable item — that's a partial success worth
    # keeping over a doubled-cost gamble.
    if chunks_total == 0 and dropped > 0 and not parsed:
        logger.warning(
            "grounded_search_json: 0 grounding chunks + all %d items dropped — "
            "retrying once with stricter prompt",
            dropped,
        )
        parsed_retry, dropped_retry, chunks_retry = await _grounded_search_once(
            prompt, model_id=model_id, is_retry=True,
        )
        if parsed_retry or chunks_retry > 0:
            logger.info(
                "grounded_search_json RETRY: recovered %d items (chunks=%d, dropped=%d)",
                len(parsed_retry), chunks_retry, dropped_retry,
            )
            parsed, dropped = parsed_retry, dropped_retry
        else:
            logger.warning(
                "grounded_search_json: retry also produced 0 grounded items — "
                "model is bypassing search; returning empty array",
            )

    if dropped:
        logger.warning(
            "grounded_search_json: dropped %d ungrounded item(s) "
            "(model produced no grounding chunks for those spans)",
            dropped,
        )

    seed_missing_url_fallbacks(parsed)
    return parsed


def seed_missing_url_fallbacks(items: list[Any]) -> None:
    """For items with ZERO URL (neither grounding nor LLM emitted one),
    seed the Google-search fallback synchronously so first-pass items
    are always clickable. Exposed so downstream refinement can reuse
    the same seed logic.
    """
    for it in items:
        if not isinstance(it, dict):
            continue
        field = active_url_field(it)
        if it.get(field):
            continue
        gs = google_search_url(it)
        if gs:
            it[field] = gs
            it["_url_source"] = "google_search"


# ── Grounding-URL helpers ──────────────────────────────────────────────


def _parse_json_array_with_spans(
    text: str,
) -> tuple[list[Any], int, list[tuple[int, int]]]:
    """Parse the first JSON array in ``text`` and return each item's
    ``(start, end)`` character span within the source text. Spans are
    needed to map `grounding_supports` (which reference text offsets)
    onto individual items.
    """
    lbracket = text.find("[")
    if lbracket < 0:
        return [], 0, []

    decoder = json.JSONDecoder()
    try:
        _, total_len = decoder.raw_decode(text[lbracket:])
    except json.JSONDecodeError:
        logger.warning(
            "grounded_search_json: JSON array parse failed: %s",
            text[lbracket : lbracket + 200],
        )
        return [], lbracket, []

    array_end = lbracket + total_len
    items: list[Any] = []
    spans: list[tuple[int, int]] = []
    i = lbracket + 1  # skip leading '['
    while i < array_end - 1:
        while i < array_end - 1 and text[i] in " \t\n\r,":
            i += 1
        if i >= array_end - 1 or text[i] == "]":
            break
        start = i
        try:
            obj, rel_end = decoder.raw_decode(text[i:array_end])
        except json.JSONDecodeError:
            break
        end = i + rel_end
        items.append(obj)
        spans.append((start, end))
        i = end
    return items, lbracket, spans


def _byte_to_char_index(text: str, byte_idx: int) -> int:
    """Convert a UTF-8 byte offset into a character offset on ``text``.

    Critical for support→item span matching: Gemini's grounding API
    returns ``segment.start_index`` / ``segment.end_index`` as BYTE
    offsets in UTF-8, NOT character offsets — verified empirically
    against a response containing an en-dash ("2–1") where
    ``end_index = char_len + 2`` matched only when interpreted as a
    byte offset. For all-ASCII text the two coincide; for CJK or any
    multibyte content (e.g. CyberNexus 赛源), char-based mapping
    silently lands supports on the wrong JSON items.

    Lossy: a byte offset that lands mid-codepoint rounds down to the
    nearest valid character boundary.
    """
    if byte_idx <= 0:
        return 0
    encoded = text.encode("utf-8")
    if byte_idx >= len(encoded):
        return len(text)
    # Walk back at most 3 bytes to find the previous valid codepoint
    # boundary if byte_idx lands mid-multibyte sequence.
    for i in range(byte_idx, max(byte_idx - 4, -1), -1):
        try:
            return len(encoded[:i].decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return 0


def _extract_grounding(response: Any, response_text: str = "") -> dict[str, Any]:
    """Pull ``grounding_chunks`` + ``grounding_supports`` + diagnostic
    metadata off the response. Returns a dict the attach step can
    consume without touching the SDK.

    ``response_text`` is needed because support segment offsets are
    UTF-8 byte indices but our caller works in character space; we
    convert here so downstream span-matching is correct for non-ASCII
    content (CJK company names, etc.).
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return {"chunks": [], "supports": [], "queries": [], "search_entry_html": ""}
    meta = getattr(candidates[0], "grounding_metadata", None)
    if meta is None:
        return {"chunks": [], "supports": [], "queries": [], "search_entry_html": ""}

    chunks_raw = getattr(meta, "grounding_chunks", None) or []
    supports_raw = getattr(meta, "grounding_supports", None) or []

    chunks: list[dict[str, Any]] = []
    for ch in chunks_raw:
        w = getattr(ch, "web", None)
        if w is None:
            chunks.append({"url": "", "title": "", "domain": ""})
            continue
        chunks.append({
            "url": getattr(w, "uri", "") or "",
            "title": getattr(w, "title", "") or "",
            "domain": getattr(w, "domain", "") or "",
        })

    supports: list[dict[str, Any]] = []
    for sup in supports_raw:
        seg = getattr(sup, "segment", None)
        if seg is None:
            continue
        # start_index may be None (= 0, start of text)
        start_byte = getattr(seg, "start_index", None)
        end_byte = getattr(seg, "end_index", None)
        if end_byte is None:
            continue
        # Convert UTF-8 byte offsets → character offsets so spans line
        # up with our character-based JSON span parsing.
        start_char = _byte_to_char_index(response_text, int(start_byte or 0))
        end_char = _byte_to_char_index(response_text, int(end_byte))
        supports.append({
            "start": start_char,
            "end": end_char,
            "chunk_indices": list(
                getattr(sup, "grounding_chunk_indices", None) or []
            ),
        })

    # Capture diagnostic metadata: search queries (debugging) and the
    # search-suggestions widget HTML (TOS — see BACKLOG entry).
    queries = list(getattr(meta, "web_search_queries", None) or [])
    sep = getattr(meta, "search_entry_point", None)
    search_entry_html = ""
    if sep is not None:
        search_entry_html = (getattr(sep, "rendered_content", None) or "")

    return {
        "chunks": chunks,
        "supports": supports,
        "queries": queries,
        "search_entry_html": search_entry_html,
    }


def _attach_grounding_urls(
    items: list[Any],
    text: str,
    spans: list[tuple[int, int]],
    grounding: dict[str, Any],
) -> None:
    """Record both the LLM's article URL and the grounding-chunk URLs
    so url_fallback can pick whichever resolves to content matching the
    item's claim.

    Why both: grounding chunks are the *sources the model cited*, not
    necessarily the *URL of the article we're emitting*. A real-world
    failure (Glacian 2026-05-01) had the LLM emit ``psu.edu/news/.../
    new-software-could-cut-cooling-energy-use-25-data-centers/`` (a real,
    verified-by-HEAD article), and the system overwrote it with a
    vertex chunk URL pointing to a different Penn State page. Throwing
    away the LLM URL when the LLM URL is the more specific anchor is
    strictly worse — and there's no signal *here* (pre-validation) about
    which is correct, so we preserve both and let url_fallback decide.

    After this function runs, an item carries:
      - ``url``: prefer LLM URL when present; else first chunk URL
      - ``_llm_url``: original LLM URL (for url_fallback to retry on)
      - ``_grounding_chunk_urls``: list of chunk URLs (for url_fallback
        to retry on)
      - ``_url_source``:
          * ``llm_with_grounding`` — LLM emitted a URL AND chunks exist
          * ``grounding`` — only chunks exist (LLM left URL blank)
          * ``no_citation`` — chunks exist but none cite this item's span
          * ``no_grounding`` — no chunks at all (model didn't ground)
    """
    chunks = grounding.get("chunks") or []
    supports = grounding.get("supports") or []

    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue

        chunk_urls: list[str] = []
        if chunks and idx < len(spans):
            span_start, span_end = spans[idx]
            seen: set[int] = set()
            for sup in supports:
                if sup["start"] < span_end and sup["end"] > span_start:
                    for ci in sup["chunk_indices"]:
                        if ci in seen:
                            continue
                        seen.add(ci)
                        if 0 <= ci < len(chunks):
                            u = chunks[ci]["url"]
                            if u:
                                chunk_urls.append(u)

        field = active_url_field(it)
        llm_url = (it.get(field) or "").strip()

        if llm_url:
            it["_llm_url"] = llm_url
        if chunk_urls:
            it["_grounding_chunk_urls"] = chunk_urls
            # Mirror the legacy field name for downstream code that
            # already knows about it (kept until callers migrate).
            it["_all_grounding_urls"] = chunk_urls

        if llm_url and chunk_urls:
            # Prefer the LLM URL as the primary candidate — it's typically
            # the more specific article URL. url_fallback will validate
            # and fall back to chunks if it doesn't content-match.
            it[field] = llm_url
            it["_url_source"] = "llm_with_grounding"
        elif chunk_urls:
            it[field] = chunk_urls[0]
            it["_url_source"] = "grounding"
        elif llm_url:
            # LLM URL but no chunks at all → no anchor backing this claim.
            it["_url_source"] = "no_grounding" if not chunks else "no_citation"
        else:
            it["_url_source"] = "no_grounding" if not chunks else "no_citation"


def active_url_field(item: dict[str, Any]) -> str:
    """Which URL field this item already carries; default to ``url``."""
    for f in URL_FIELDS:
        if f in item:
            return f
    return "url"


def google_search_url(item: dict[str, Any]) -> str:
    """Build a `google.com/search?q=<anchor>` URL as a guaranteed-
    clickable fallback when no real URL is usable.
    """
    for key in _ANCHOR_FIELDS:
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return "https://www.google.com/search?q=" + quote_plus(v.strip())
    return ""


# ── Path 3 — Interactions API (chat) ──────────────────────────────────
#
# The Interactions API expects `tools`, `system_instruction`, and
# `previous_interaction_id` as top-level kwargs on
# `client.interactions.create`. Tools are plain dicts, not
# `types.Tool(...)`:
#     function:      {"type":"function","name":...,"description":...,"parameters":{...}}
#     google_search: {"type":"google_search"}
#
# Agentic loop is client-driven: the model returns `function_call`
# outputs, we execute them locally and feed `function_result` blocks
# back via a fresh create() with the previous interaction id. We loop
# until the model produces only text (or hit the safety cap).


async def interactions_create(
    *,
    model: str,
    input: Any,
    previous_interaction_id: str | None = None,
    system_instruction: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_attempts: int = 3,
) -> Any:
    """Thin async wrapper over `client.interactions.create`.

    The Interactions API is sync-only in google-genai 1.69 so we hop
    through `asyncio.to_thread` to avoid blocking the event loop.
    Returns the raw interaction object; callers inspect `outputs` and
    `id`.

    `max_attempts=1` skips the retry loop — callers use that when
    they want to fail fast and recover at a higher level (e.g. the
    stale-`previous_interaction_id` fallback in scholar_chat).
    """
    client = genai_client()

    def _call():
        kwargs: dict[str, Any] = {"model": model, "input": input}
        if previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if tools:
            kwargs["tools"] = tools
        return client.interactions.create(**kwargs)

    async def _awaited():
        return await asyncio.to_thread(_call)

    return await _with_retry(_awaited, max_attempts=max_attempts)


def extract_interaction_text(response: Any) -> str:
    """Concatenate every `text` block from an interaction response."""
    outputs = getattr(response, "outputs", None) or []
    parts: list[str] = []
    for item in outputs:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def extract_function_calls(response: Any) -> list[Any]:
    """Return output blocks whose type is `function_call`."""
    outputs = getattr(response, "outputs", None) or []
    calls: list[Any] = []
    for item in outputs:
        t = getattr(item, "type", None)
        if t == "function_call":
            calls.append(item)
    return calls


def parse_function_call_args(call: Any) -> dict[str, Any]:
    """Normalise `call.arguments` — docs show dict, SDK may return str."""
    args = getattr(call, "arguments", None)
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
