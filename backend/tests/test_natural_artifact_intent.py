"""Natural-language create vs edit intent (English/CN/mixed) and apply-tool gate.

Uses real entity IDs from the workspace when ``data/vc_portfolio.db`` is present.
Gate-only cases do not write to the database. One case uses a bogus artifact id so
the tool fails at resolve after the gate (no mutation).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.config import settings
from app.services.portfolio_deep_agent import (
    _looks_like_create_intent,
    _looks_like_explicit_edit_intent,
    build_portfolio_tools,
)

# Real portfolio entities (see user workspace data)
E_KIWI = "c2204fda-e431-4e1d-a6f0-4a35122fd9d3"
E_BOT_AUTO = "bda87e02-62f9-41d1-90fb-d84026d5fd45"
KIWI_EXTRACT_LATEST = "e7dbcb3e-0e4f-495e-9573-6fccbf3081f9"
FAKE_ARTIFACT_ID = "00000000-0000-0000-0000-000000000099"

_REPO_DATA_DB = pathlib.Path(__file__).resolve().parents[2] / "data" / "vc_portfolio.db"
needs_data_db = pytest.mark.skipif(
    not _REPO_DATA_DB.exists(),
    reason=f"integration: missing {_REPO_DATA_DB}",
)


def _tool_by_name(tools: list, name: str):
    for t in tools:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"tool not found: {name}")


@pytest.mark.parametrize(
    "text,expect_create",
    [
        # English — informal persistence (user examples)
        (
            "can you summarize our discussion on dilution and keep it in record",
            True,
        ),
        ("take a note — SAFE favors founders, cap table still messy", True),
        ("pls jot down: Kiwi follow-up is legal review next week", True),
        ("summarize the trucking angle for the file", True),
        ("capture this discussion on governance; keep on file somewhere", True),
        # Chinese / mixed
        ("把我们刚聊的回购条款总结一下记在档案里呗", True),
        ("帮我memo一下：章程和股东协议对上了没", True),
        ("总结今天聊天 main risks 然后留下来后面用", True),
        ("workspace里记一下：增资协议数字还要核对", True),
        # Explicit artifact wording (still “save new” vibe)
        ("把这个结论保存成artifact吧", True),
        # Negative — answer-only
        ("what's the interest rate in the SPA?", False),
        ("Just summarize the cap table for me here", False),
    ],
)
def test_create_intent_heuristic(text: str, expect_create: bool):
    assert _looks_like_create_intent(text) is expect_create


@pytest.mark.parametrize(
    "text,expect_edit",
    [
        ("update info with the new liquidation prefs", True),
        ("revise extract_info — founders section only", True),
        ("帮我把信息表更新一下 latest SAFE", True),
        ("加上刚才那段回购评价到 extract_info", True),
        # Mixed
        ("Patch the JSON 然后 keep titles consistent pls", True),
        # Negative
        ("any red flags in the shareholder agreement?", False),
    ],
)
def test_explicit_edit_intent_heuristic(text: str, expect_edit: bool):
    assert _looks_like_explicit_edit_intent(text) is expect_edit


@needs_data_db
def test_apply_gate_blocks_fuzzy_save_without_artifact_context(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY", "create_new")
    tools = build_portfolio_tools(
        E_KIWI,
        session_id="00000000-0000-0000-0000-000000000001",
        session_artifact_ids=[],
        run_id=None,
        initial_user_text="take a note on buyback: founder-friendly, pls keep in workspace",
    )
    apply = _tool_by_name(tools, "portfolio_apply_artifact_edit")
    out = apply.invoke(
        {
            "artifact_id": KIWI_EXTRACT_LATEST,
            "new_content": "{}",
            "mode": "versioned",
            "user_context": "",
        }
    )
    data = json.loads(out)
    assert data.get("error") == "create_intent_requires_create_tool"


@needs_data_db
def test_apply_gate_allows_when_user_explicitly_edits(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY", "create_new")
    tools = build_portfolio_tools(
        E_BOT_AUTO,
        session_id="00000000-0000-0000-0000-000000000002",
        session_artifact_ids=[],
        run_id=None,
        initial_user_text="update extract_info with Bot Auto SAFE date from the docx",
    )
    apply = _tool_by_name(tools, "portfolio_apply_artifact_edit")
    out = apply.invoke(
        {
            "artifact_id": FAKE_ARTIFACT_ID,
            "new_content": "{}",
            "mode": "versioned",
            "user_context": "",
        }
    )
    data = json.loads(out)
    assert data.get("error") != "create_intent_requires_create_tool"
    assert data.get("ok") is False
    assert data.get("error") == "target_not_resolved"


@needs_data_db
def test_apply_gate_allows_when_artifact_selected_in_turn(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY", "create_new")
    tools = build_portfolio_tools(
        E_KIWI,
        session_id="00000000-0000-0000-0000-000000000003",
        session_artifact_ids=[KIWI_EXTRACT_LATEST],
        run_id=None,
        initial_user_text="保存到当前打开的档案：把这段补进去",  # create-ish + explicit target in UI
    )
    apply = _tool_by_name(tools, "portfolio_apply_artifact_edit")
    out = apply.invoke(
        {
            "artifact_id": FAKE_ARTIFACT_ID,
            "new_content": "{}",
            "mode": "versioned",
            "user_context": "",
        }
    )
    data = json.loads(out)
    assert data.get("error") != "create_intent_requires_create_tool"
    assert data.get("ok") is False
    assert data.get("error") == "target_not_resolved"


@needs_data_db
def test_policy_allow_edit_skips_gate(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY", "allow_edit")
    tools = build_portfolio_tools(
        E_KIWI,
        session_id="00000000-0000-0000-0000-000000000004",
        session_artifact_ids=[],
        run_id=None,
        initial_user_text="keep a record: test only",
    )
    apply = _tool_by_name(tools, "portfolio_apply_artifact_edit")
    out = apply.invoke(
        {
            "artifact_id": FAKE_ARTIFACT_ID,
            "new_content": "{}",
            "mode": "versioned",
            "user_context": "",
        }
    )
    data = json.loads(out)
    assert data.get("error") != "create_intent_requires_create_tool"
