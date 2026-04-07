"""Direct LLM calls: Gemini (Interactions API + one-shot) and Kimi (OpenAI-compatible).

Replaces gemini_runner.py. Single module for all non-LangChain LLM calls.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import List, Optional, Sequence, Tuple

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)


# ── Shared ────────────────────────────────────────────────────


def _get_client() -> genai.Client:
    key = (settings.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set. Add it to your environment or .env"
        )
    return genai.Client(api_key=key)


def _extract_text(response) -> str:
    """Extract text from a Gemini response (generate_content or interactions.create)."""
    text = getattr(response, "text", None) or ""
    if text.strip():
        return text.strip()
    # Fallback: concatenate candidate parts
    candidates = getattr(response, "candidates", None)
    if candidates:
        parts_out = []
        for c in candidates:
            if c.content and c.content.parts:
                for p in c.content.parts:
                    if p.text:
                        parts_out.append(p.text)
        if parts_out:
            return "\n".join(parts_out).strip()
    return ""


def _retry(fn, max_attempts: int = 3):
    """Call fn() with exponential backoff. Returns result or raises last error."""
    last_err: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            logger.warning("LLM attempt %s failed: %s", attempt + 1, e)
            time.sleep(1.0 * (2 ** attempt))
    raise RuntimeError(f"LLM call failed after {max_attempts} retries: {last_err}")


def _history_to_contents(
    history: Sequence[Tuple[str, str]],
) -> List[types.Content]:
    out: List[types.Content] = []
    for role, text in history:
        r = "user" if role == "user" else "model"
        if not text.strip():
            continue
        out.append(types.Content(role=r, parts=[types.Part.from_text(text=text)]))
    return out


# ── Gemini: Interactions API (session-stateful chat) ──────────


def _extract_interaction_text(response) -> str:
    """Extract text from an Interactions API response."""
    outputs = getattr(response, "outputs", None)
    if not outputs:
        return ""
    parts = []
    for item in outputs:
        # TextContent has .text; other types have .type
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _context_parts_to_interaction_content(
    context_parts: List[types.Part],
) -> List[dict]:
    """Convert Gemini types.Part objects to Interactions API content dicts.

    Text parts → TextContentParam. Binary parts (inline_data) → appropriate
    content type. Unsupported parts are converted to text descriptions.
    """
    items: List[dict] = []
    for part in context_parts:
        if part.text:
            items.append({"type": "text", "text": part.text})
        elif part.inline_data:
            mime = part.inline_data.mime_type or "application/octet-stream"
            data_b64 = base64.b64encode(part.inline_data.data).decode("ascii")
            if mime == "application/pdf":
                items.append({"type": "document", "data": data_b64, "mime_type": mime})
            elif mime.startswith("image/"):
                items.append({"type": "image", "data": data_b64, "mime_type": mime})
            else:
                items.append({"type": "text", "text": f"[Binary attachment: {mime}]"})
    return items


def generate_with_interaction(
    system_instruction: str,
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    previous_interaction_id: Optional[str] = None,
    history_for_fresh_chain: Optional[Sequence[Tuple[str, str]]] = None,
) -> Tuple[str, str]:
    """
    Interactions API call. Returns (reply_text, new_interaction_id).

    If previous_interaction_id is valid: sends only the new turn
    (Gemini remembers full context server-side).
    If None: includes history_for_fresh_chain in input for context.
    """
    client = _get_client()
    model = settings.GEMINI_MODEL
    use_search = (
        settings.CHAT_ENABLE_GOOGLE_SEARCH
        if enable_google_search is None
        else enable_google_search
    )

    # Build input as list of Turns (Interactions API format)
    turns: List[dict] = []

    # For fresh chains, prepend history as Turn objects
    if not previous_interaction_id and history_for_fresh_chain:
        for role, text in history_for_fresh_chain:
            if not text.strip():
                continue
            # Interactions API uses "model" for assistant role
            r = "user" if role == "user" else "model"
            turns.append({"role": r, "content": text})

    # Current user turn — text + optional multimodal attachments
    if context_parts:
        user_content: List[dict] = [{"type": "text", "text": user_message_text}]
        user_content.extend(_context_parts_to_interaction_content(context_parts))
        turns.append({"role": "user", "content": user_content})
    else:
        turns.append({"role": "user", "content": user_message_text})

    # Build tools for Interactions API (different type from types.Tool)
    tools = None
    if use_search:
        tools = [{"type": "google_search"}]

    kwargs: dict = {
        "model": model,
        "input": turns,
        "system_instruction": system_instruction,
    }
    if tools:
        kwargs["tools"] = tools
    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id

    def _call():
        response = client.interactions.create(**kwargs)
        text = _extract_interaction_text(response)
        if not text:
            text = "(No text returned from model.)"
        interaction_id = getattr(response, "id", None) or ""
        return text, str(interaction_id)

    return _retry(_call)


# ── Gemini: One-shot (presets, metadata, summarization) ───────


def generate_one_shot(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    model: Optional[str] = None,
) -> str:
    """Stateless generate_content call. Replaces generate_with_context."""
    client = _get_client()
    resolved_model = model or settings.GEMINI_MODEL
    use_search = (
        settings.CHAT_ENABLE_GOOGLE_SEARCH
        if enable_google_search is None
        else enable_google_search
    )

    contents = _history_to_contents(history)
    user_parts: List[types.Part] = [types.Part.from_text(text=user_message_text)]
    if context_parts:
        user_parts.extend(context_parts)
    contents.append(types.Content(role="user", parts=user_parts))

    tools = None
    if use_search:
        tools = [types.Tool(google_search=types.GoogleSearch())]

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools,
    )

    def _call():
        response = client.models.generate_content(
            model=resolved_model,
            contents=contents,
            config=config,
        )
        text = _extract_text(response)
        return text if text else "(No text returned from model.)"

    return _retry(_call)


def generate_json_one_shot(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    model: Optional[str] = None,
) -> str:
    """JSON-constrained one-shot. Replaces generate_json_with_context."""
    client = _get_client()
    resolved = (model or settings.GEMINI_METADATA_EXTRACTION_MODEL or "").strip()
    if not resolved:
        resolved = settings.GEMINI_MODEL
    use_search = (
        settings.CHAT_ENABLE_GOOGLE_SEARCH
        if enable_google_search is None
        else enable_google_search
    )

    contents = _history_to_contents(history)
    user_parts: List[types.Part] = [types.Part.from_text(text=user_message_text)]
    if context_parts:
        user_parts.extend(context_parts)
    contents.append(types.Content(role="user", parts=user_parts))

    tools = None
    if use_search:
        tools = [types.Tool(google_search=types.GoogleSearch())]

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools,
        response_mime_type="application/json",
        temperature=0.1,
    )

    def _call():
        response = client.models.generate_content(
            model=resolved,
            contents=contents,
            config=config,
        )
        text = _extract_text(response)
        return text if text else "{}"

    return _retry(_call)


# ── Kimi: OpenAI-compatible (stateless chat) ──────────────────


def generate_with_kimi(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
) -> str:
    """OpenAI-compatible chat call to Kimi/Moonshot. Stateless."""
    from openai import OpenAI
    from app.services.model_profiles import _kimi_openai_credentials, _kimi_code_request_extras

    api_key, base_url = _kimi_openai_credentials()
    model_id, extra_headers = _kimi_code_request_extras(base_url)

    client = OpenAI(api_key=api_key, base_url=base_url)

    messages = [{"role": "system", "content": system_instruction}]
    for role, text in history:
        if not text.strip():
            continue
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_message_text})

    extra_body = {}
    if settings.KIMI_DISABLE_THINKING_FOR_SEARCH:
        extra_body = {"thinking": {"type": "disabled"}, "enable_thinking": False}

    def _call():
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            extra_headers=extra_headers or None,
            extra_body=extra_body or None,
        )
        return (response.choices[0].message.content or "").strip()

    return _retry(_call)
