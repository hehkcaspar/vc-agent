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

from google import genai
from google.genai import types
from pydantic import BaseModel

from ...config import settings

logger = logging.getLogger(__name__)

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


async def grounded_search_json(
    prompt: str,
    *,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Grounded-search single-shot call that returns a parsed JSON array.

    Used by sources/news_web.py and sources/red_flags_watch.py where
    the response schema is untyped list-of-dicts. We parse the text
    directly because Gemini cannot always combine `response_schema`
    with the google_search tool.
    """
    client = genai_client()
    model_id = model or settings.ACADEMIC_GEMINI_MODEL

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )

    async def _call():
        return await client.aio.models.generate_content(
            model=model_id,
            contents=_parts([prompt]),
            config=config,
        )

    response = await _with_retry(_call)
    text = _extract_text(response)
    if not text:
        return []
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        logger.warning("grounded_search_json: failed to parse JSON: %s", text[:200])
        return []
    return data if isinstance(data, list) else []


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
