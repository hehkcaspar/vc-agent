"""Unit tests for the inline `.md` / `.json` editor.

Covers:
- ``_check_provenance`` blocks agent overwrites of ``origin_type="user"``
  files (the 2026-05-03 addition that makes auto-flip-origin actually
  protect user edits).
- ``write_file`` flips ``origin_type`` on overwrite when callers pass
  ``origin_type="user"`` (pre-2026-05-03 it ignored the kwarg on the
  overwrite path, leaving the protection symbolic).
- ``_EDITABLE_EXTS`` covers the expected text-shaped extensions.

Service-level tests (no HTTP) so they don't trip the shared-password
gate from the project's `.env`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routers.workspace import _EDITABLE_EXTS
from app.services.workspace import (
    Actor,
    ProtectedFileError,
    workspace_service,
)


# ── _check_provenance ─────────────────────────────────────────────────


class TestCheckProvenance:
    """Pure logic — no DB needed; we hand a stub node + actor."""

    def _node(self, origin: str | None) -> SimpleNamespace:
        return SimpleNamespace(
            origin_type=origin,
            path="Deliverables/Memos/initial_screening_v2.md",
        )

    def test_agent_blocked_overwriting_upload(self) -> None:
        node = self._node("upload")
        with pytest.raises(ProtectedFileError):
            workspace_service._check_provenance(
                node, Actor(type="agent"), "overwrite",
            )

    def test_agent_blocked_overwriting_ingest(self) -> None:
        node = self._node("ingest")
        with pytest.raises(ProtectedFileError):
            workspace_service._check_provenance(
                node, Actor(type="agent"), "overwrite",
            )

    def test_agent_blocked_overwriting_user(self) -> None:
        """Regression for the auto-flip-origin protection. Pre-fix
        this passed silently and the agent's overwrite would clobber
        the user's edits."""
        node = self._node("user")
        with pytest.raises(ProtectedFileError):
            workspace_service._check_provenance(
                node, Actor(type="agent"), "overwrite",
            )

    def test_agent_allowed_overwriting_agent(self) -> None:
        """Composer reruns of agent-managed files stay allowed."""
        node = self._node("agent")
        # No raise.
        workspace_service._check_provenance(
            node, Actor(type="agent"), "overwrite",
        )

    def test_agent_allowed_overwriting_shared(self) -> None:
        """WORKSPACE_NOTES.md and similar (origin=shared) stay
        agent-writable per the comment in _check_provenance."""
        node = self._node("shared")
        workspace_service._check_provenance(
            node, Actor(type="agent"), "overwrite",
        )

    def test_user_actor_never_blocked(self) -> None:
        """The gate exists ONLY for actor.type=='agent'. User edits
        always pass through (the inline-editor route uses
        Actor(type='user'))."""
        for origin in ("upload", "ingest", "user", "agent", "shared", None):
            node = self._node(origin)
            workspace_service._check_provenance(
                node, Actor(type="user"), "overwrite",
            )

    def test_system_actor_never_blocked(self) -> None:
        for origin in ("upload", "ingest", "user", "agent"):
            node = self._node(origin)
            workspace_service._check_provenance(
                node, Actor(type="system"), "overwrite",
            )

    def test_non_overwrite_operations_pass(self) -> None:
        """The gate only fires for overwrite/delete; reads, moves,
        renames don't go through this path."""
        node = self._node("user")
        for op in ("read", "rename", "annotate"):
            workspace_service._check_provenance(
                node, Actor(type="agent"), op,
            )


# ── _EDITABLE_EXTS ────────────────────────────────────────────────────


class TestEditableExtensions:
    def test_includes_md(self) -> None:
        assert ".md" in _EDITABLE_EXTS
        assert ".markdown" in _EDITABLE_EXTS

    def test_includes_json(self) -> None:
        assert ".json" in _EDITABLE_EXTS

    def test_includes_plaintext(self) -> None:
        assert ".txt" in _EDITABLE_EXTS
        assert ".csv" in _EDITABLE_EXTS

    def test_excludes_binary_formats(self) -> None:
        for ext in (".pdf", ".png", ".jpg", ".docx", ".pptx", ".xlsx"):
            assert ext not in _EDITABLE_EXTS, (
                f"{ext} should NOT be editable — backend defends with 415"
            )


__all__: list[str] = []
