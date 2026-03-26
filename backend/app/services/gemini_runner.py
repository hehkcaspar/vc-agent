"""Call Gemini (google-genai) with optional Google Search."""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional, Sequence, Tuple

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    key = (settings.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set. Add it to your environment or .env"
        )
    return genai.Client(api_key=key)


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


def generate_with_context(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
) -> str:
    """
    Single generate_content call. history is (role, text) with role user|assistant.
    context_parts are appended to the final user message (files, excerpts).
    """
    client = _get_client()
    model = settings.GEMINI_MODEL
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

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            text = getattr(response, "text", None) or ""
            if text.strip():
                return text.strip()
            # Fallback: concatenate candidate parts
            if response.candidates:
                parts_out = []
                for c in response.candidates:
                    if c.content and c.content.parts:
                        for p in c.content.parts:
                            if p.text:
                                parts_out.append(p.text)
                if parts_out:
                    return "\n".join(parts_out).strip()
            return "(No text returned from model.)"
        except Exception as e:
            last_err = e
            logger.warning("Gemini attempt %s failed: %s", attempt + 1, e)
            time.sleep(1.0 * (2**attempt))

    raise RuntimeError(f"Gemini failed after retries: {last_err}")


def generate_json_with_context(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    model: Optional[str] = None,
) -> str:
    """Like generate_with_context but forces JSON output (application/json).

    Uses ``GEMINI_METADATA_EXTRACTION_MODEL`` by default (see ``app.config``).
    """
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

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=resolved,
                contents=contents,
                config=config,
            )
            text = getattr(response, "text", None) or ""
            if text.strip():
                return text.strip()
            if response.candidates:
                parts_out = []
                for c in response.candidates:
                    if c.content and c.content.parts:
                        for p in c.content.parts:
                            if p.text:
                                parts_out.append(p.text)
                if parts_out:
                    return "\n".join(parts_out).strip()
            return "{}"
        except Exception as e:
            last_err = e
            logger.warning("Gemini JSON attempt %s failed: %s", attempt + 1, e)
            time.sleep(1.0 * (2**attempt))

    raise RuntimeError(f"Gemini JSON failed after retries: {last_err}")
