"""Chat model profiles for Deep Agents: Gemini (LC) + Kimi OpenAI-compatible."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.config import settings

ProfileId = Literal["gemini_google", "kimi_moonshot"]


def _kimi_thinking_disabled_body() -> dict[str, Any]:
    """
    Kimi K2.5 with thinking on expects `reasoning_content` on prior assistant tool
    messages when replaying history. LangGraph / Deep Agents omit it → 400
    ``reasoning_content is missing in assistant tool call message``.

    Moonshot docs: ``thinking`` is ``{"type": "disabled"}`` or ``{"type": "enabled"}``.
    Kimi Code also honors ``enable_thinking: false`` on some routes.
    """
    return {"thinking": {"type": "disabled"}, "enable_thinking": False}


def _kimi_openai_credentials() -> tuple[str, str]:
    """
    Return (api_key, base_url) for ChatOpenAI / Moonshot-compatible endpoints.

    Kimi Code keys (Kimi CLI ``/login`` → Kimi Code) use api.kimi.com/coding/v1;
    Moonshot Open Platform uses api.moonshot.ai/v1 (or .cn). Wrong host → 401.

    Resolution: ``KIMI_OPENAI_BASE_URL`` if set; else MOONSHOT_API_KEY →
    ``MOONSHOT_BASE_URL``; else KIMI_CODE_API_KEY → ``KIMI_CODE_BASE_URL``.
    """
    moon = (settings.MOONSHOT_API_KEY or os.getenv("MOONSHOT_API_KEY") or "").strip()
    code = (settings.KIMI_CODE_API_KEY or os.getenv("KIMI_CODE_API_KEY") or "").strip()
    key = moon or code
    if not key:
        raise ValueError(
            "MOONSHOT_API_KEY or KIMI_CODE_API_KEY is required for the kimi_moonshot "
            "harness profile (OpenAI-compatible /v1/chat/completions)."
        )
    override = (
        settings.KIMI_OPENAI_BASE_URL or os.getenv("KIMI_OPENAI_BASE_URL") or ""
    ).strip().rstrip("/")
    if override:
        return key, override
    if moon:
        base = (
            settings.MOONSHOT_BASE_URL or os.getenv("MOONSHOT_BASE_URL") or ""
        ).strip().rstrip("/") or "https://api.moonshot.ai/v1"
        return key, base
    base = (
        settings.KIMI_CODE_BASE_URL or os.getenv("KIMI_CODE_BASE_URL") or ""
    ).strip().rstrip("/") or "https://api.kimi.com/coding/v1"
    return key, base


def _kimi_code_request_extras(base_url: str) -> tuple[str, dict]:
    """Model id + default_headers for ChatOpenAI. Kimi For Coding rejects generic clients (403)."""
    if "kimi.com/coding" in base_url:
        model = (settings.KIMI_CODE_MODEL or "").strip() or settings.MOONSHOT_MODEL
        # /chat/completions checks User-Agent; see KIMI_CODE_HTTP_USER_AGENT (must match allowed clients).
        ua = (settings.KIMI_CODE_HTTP_USER_AGENT or "").strip() or "KimiCLI/1.6"
        return model, {"User-Agent": ua}
    return settings.MOONSHOT_MODEL, {}


@dataclass(frozen=True)
class ModelProfile:
    profile_id: ProfileId
    provider: str
    model_name: str


def _google_api_key() -> str:
    key = (settings.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is required for the Gemini harness profile"
        )
    return key


def build_chat_model(
    profile_id: Optional[str] = None,
    *,
    enable_google_search: Optional[bool] = None,
    require_search_capable: bool = False,
) -> BaseChatModel:
    """
    Build a LangChain chat model for create_deep_agent.

    Kimi: ``KIMI_DISABLE_THINKING_FOR_SEARCH`` adds ``thinking: {type: disabled}`` (
    required for tool/multi-turn compatibility). ``require_search_capable`` is reserved.
    """
    pid = profile_id or settings.CHAT_DEFAULT_MODEL_PROFILE
    use_search = (
        settings.CHAT_ENABLE_GOOGLE_SEARCH
        if enable_google_search is None
        else enable_google_search
    )

    if pid == "kimi_moonshot":
        key, base_url = _kimi_openai_credentials()
        model, extra_headers = _kimi_code_request_extras(base_url)
        kw: dict = {
            "api_key": key,
            "base_url": base_url,
            "model": model,
        }
        if extra_headers:
            kw["default_headers"] = extra_headers
        # Thinking + tool history is brittle for Kimi; disable unless explicitly allowed.
        if settings.KIMI_DISABLE_THINKING_FOR_SEARCH:
            kw["extra_body"] = _kimi_thinking_disabled_body()
        return ChatOpenAI(**kw)

    # Default: Gemini via LangChain
    model = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=_google_api_key(),
    )
    if use_search:
        model = model.bind_tools([{"google_search": {}}])
    return model


def build_deep_agent_base_chat_model(
    profile_id: Optional[str] = None,
) -> BaseChatModel:
    """
    Model for `create_deep_agent`: must be a plain `BaseChatModel`.

    Do not use `bind_tools` here — RunnableBinding is not a `BaseChatModel` and
    `deepagents.resolve_model` will break (`startswith` on wrong type).
    """
    pid = normalize_profile_id(profile_id)
    if pid == "kimi_moonshot":
        key, base_url = _kimi_openai_credentials()
        model, extra_headers = _kimi_code_request_extras(base_url)
        kw: dict = {
            "api_key": key,
            "base_url": base_url,
            "model": model,
        }
        if extra_headers:
            kw["default_headers"] = extra_headers
        # Deep Agent = multi-turn tool calls; Kimi must not use thinking (see _kimi_thinking_disabled_body).
        if settings.KIMI_DISABLE_THINKING_FOR_SEARCH:
            kw["extra_body"] = _kimi_thinking_disabled_body()
        return ChatOpenAI(**kw)
    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=_google_api_key(),
    )


def normalize_profile_id(raw: Optional[str]) -> ProfileId:
    """Resolve harness profile: per-message override, else `CHAT_DEFAULT_MODEL_PROFILE`."""
    pid = (raw or "").strip() or (settings.CHAT_DEFAULT_MODEL_PROFILE or "").strip()
    if pid == "kimi_moonshot":
        return "kimi_moonshot"
    # Any other value (including `gemini_google`, empty, or typo) falls back to Gemini.
    return "gemini_google"
