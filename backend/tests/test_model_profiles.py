"""Unit tests for Kimi / Moonshot URL resolution (Kimi Code vs Open Platform)."""

from __future__ import annotations

import pytest

from app.config import settings
from app.services.model_profiles import _kimi_openai_credentials


def test_kimi_openai_credentials_kimi_code_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MOONSHOT_API_KEY", "")
    monkeypatch.setattr(settings, "KIMI_CODE_API_KEY", "sk-code-test")
    monkeypatch.setattr(settings, "KIMI_OPENAI_BASE_URL", "")
    monkeypatch.setattr(settings, "KIMI_CODE_BASE_URL", "https://api.kimi.com/coding/v1")
    k, b = _kimi_openai_credentials()
    assert k == "sk-code-test"
    assert "kimi.com/coding" in b


def test_kimi_openai_credentials_open_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MOONSHOT_API_KEY", "sk-moon-test")
    monkeypatch.setattr(settings, "KIMI_CODE_API_KEY", "")
    monkeypatch.setattr(settings, "KIMI_OPENAI_BASE_URL", "")
    monkeypatch.setattr(settings, "MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    k, b = _kimi_openai_credentials()
    assert k == "sk-moon-test"
    assert "moonshot.ai" in b


def test_kimi_openai_credentials_open_platform_cn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MOONSHOT_API_KEY", "sk-cn")
    monkeypatch.setattr(settings, "KIMI_CODE_API_KEY", "")
    monkeypatch.setattr(settings, "KIMI_OPENAI_BASE_URL", "")
    monkeypatch.setattr(settings, "MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
    _, b = _kimi_openai_credentials()
    assert "moonshot.cn" in b


def test_kimi_openai_credentials_override_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MOONSHOT_API_KEY", "sk-x")
    monkeypatch.setattr(settings, "KIMI_CODE_API_KEY", "")
    monkeypatch.setattr(settings, "KIMI_OPENAI_BASE_URL", "https://example.com/v1")
    k, b = _kimi_openai_credentials()
    assert k == "sk-x"
    assert b == "https://example.com/v1"


def test_kimi_openai_credentials_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MOONSHOT_API_KEY", "")
    monkeypatch.setattr(settings, "KIMI_CODE_API_KEY", "")
    with pytest.raises(ValueError, match="MOONSHOT_API_KEY"):
        _kimi_openai_credentials()
