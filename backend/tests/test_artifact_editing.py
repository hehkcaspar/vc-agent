"""Unit tests for Option B artifact editing (sync DB + storage)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Artifact, ArtifactEditEvent, Base, Entity, Resource
from app.services import storage as storage_mod
from app.services.artifact_editing import (
    apply_artifact_edit,
    list_entity_artifacts_sync,
    read_resource_payload_sync,
    resolve_artifact_target,
    resolve_edit_mode,
    validate_edit_payload,
)
from app.services.artifact_service import create_artifact_for_entity_sync
from app.services.storage import LocalFilesystemAdapter


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    root = tmp_path / "entities"
    root.mkdir(parents=True)
    adapter = LocalFilesystemAdapter(root)
    monkeypatch.setattr(storage_mod, "storage", adapter)
    return root


@pytest.fixture
def db_session(tmp_path, isolated_storage):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True)
    return Session


def _seed_entity(session, eid: str = "ent-1") -> None:
    session.add(Entity(id=eid, type="company", name="Acme"))
    session.commit()


def test_resolve_by_artifact_id(db_session):
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(db, "ent-1", "memo", "v1 body", title="memo_a")
        aid = a.id
    with db_session() as db:
        r = resolve_artifact_target(db, "ent-1", artifact_id=aid)
        assert r["ok"] is True
        assert r["artifact"].id == aid


def test_resolve_unknown_id(db_session):
    with db_session() as db:
        _seed_entity(db)
        create_artifact_for_entity_sync(db, "ent-1", "memo", "x", title="t")
        r = resolve_artifact_target(db, "ent-1", artifact_id=str(uuid.uuid4()))
        assert r["ok"] is False
        assert r["reason"] == "id_not_found"


def test_validate_rejects_oversize(db_session, monkeypatch):
    monkeypatch.setattr("app.services.artifact_editing.settings.CHAT_MAX_ARTIFACT_CHARS", 10)
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(db, "ent-1", "memo", "short", title="t")
        v = validate_edit_payload("x" * 50, target=a)
        assert v["ok"] is False


def test_validate_json_artifact(db_session):
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(
            db, "ent-1", "other", "{}", title="j", file_suffix=".json"
        )
        bad = validate_edit_payload("{not json", target=a)
        assert bad["ok"] is False
        good = validate_edit_payload('{"a": 1}', target=a)
        assert good["ok"] is True


def test_versioned_apply_creates_new_row(db_session):
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(db, "ent-1", "memo", "orig", title="line")
        old_id = a.id
        out = apply_artifact_edit(
            db,
            correlation_id="corr-1",
            entity_id="ent-1",
            session_id="sess-1",
            target=a,
            new_content="updated",
            mode="versioned",
            run_id="run-1",
        )
        assert out["ok"] is True
        assert out["mode"] == "versioned"
        assert out["artifact_id"] != old_id
        rows = list_entity_artifacts_sync(db, "ent-1")
        assert len(rows) == 2
        latest = max(rows, key=lambda x: x.version)
        assert latest.version == 2


def test_overwrite_when_disabled_falls_back_to_versioned(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_editing.settings.CHAT_ARTIFACT_OVERWRITE_ENABLED",
        False,
    )
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(db, "ent-1", "memo", "x", title="t")
        out = apply_artifact_edit(
            db,
            correlation_id="c2",
            entity_id="ent-1",
            session_id=None,
            target=a,
            new_content="y",
            mode="overwrite",
            explicit_mode="overwrite",
            run_id=None,
        )
        assert out["ok"] is True
        assert out["mode"] == "versioned"


def test_overwrite_applies_in_place(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_editing.settings.CHAT_ARTIFACT_OVERWRITE_ENABLED",
        True,
    )
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(db, "ent-1", "memo", "orig", title="t")
        aid = a.id
        out = apply_artifact_edit(
            db,
            correlation_id="c3",
            entity_id="ent-1",
            session_id=None,
            target=a,
            new_content="replaced",
            mode="overwrite",
            explicit_mode="overwrite",
        )
        assert out["ok"] is True
        assert out["mode"] == "overwrite"
        assert out["artifact_id"] == aid
        row = db.execute(select(Artifact).where(Artifact.id == aid)).scalar_one()
        raw = storage_mod.storage.read_file_sync(row.relative_path)
        assert raw.decode() == "replaced"


def test_audit_rows_written(db_session):
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(db, "ent-1", "memo", "z", title="z")
        apply_artifact_edit(
            db,
            correlation_id="audit-me",
            entity_id="ent-1",
            session_id="s",
            target=a,
            new_content="nv",
            mode="versioned",
        )
        evs = db.execute(
            select(ArtifactEditEvent).where(
                ArtifactEditEvent.correlation_id == "audit-me"
            )
        ).scalars().all()
        states = {e.state for e in evs}
        assert "applied" in states
        assert "intent_received" in states


def test_resolve_edit_mode_respects_overwrite_flag(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_editing.settings.CHAT_ARTIFACT_OVERWRITE_ENABLED",
        False,
    )
    assert (
        resolve_edit_mode(user_text="please overwrite in place", explicit_mode=None)
        == "versioned"
    )


def test_apply_invalid_json_does_not_add_artifact_row(db_session):
    with db_session() as db:
        _seed_entity(db)
        a = create_artifact_for_entity_sync(
            db, "ent-1", "other", "{}", title="j", file_suffix=".json"
        )
        n_before = len(list_entity_artifacts_sync(db, "ent-1"))
        out = apply_artifact_edit(
            db,
            correlation_id="val-fail",
            entity_id="ent-1",
            session_id=None,
            target=a,
            new_content="{not json",
            mode="versioned",
        )
        assert out["ok"] is False
        n_after = len(list_entity_artifacts_sync(db, "ent-1"))
        assert n_after == n_before


def test_read_resource_payload_text_file(db_session):
    with db_session() as db:
        _seed_entity(db)
        rel = "ent-1/resources/r1/note.txt"
        storage_mod.storage.ensure_dir_sync("ent-1/resources/r1")
        storage_mod.storage.write_file_sync(rel, b"hello resource")
        db.add(
            Resource(
                id="r1",
                entity_id="ent-1",
                resource_type="file",
                title="note",
                relative_path=rel,
                mime_type="text/plain",
            )
        )
        db.commit()
    with db_session() as db:
        out = read_resource_payload_sync(db, "ent-1", "r1")
        assert out["ok"] is True
        assert out["text"] == "hello resource"


def test_normalize_profile_uses_settings_default(monkeypatch):
    from app.services import model_profiles as mp

    monkeypatch.setattr(mp.settings, "CHAT_DEFAULT_MODEL_PROFILE", "kimi_moonshot")
    assert mp.normalize_profile_id(None) == "kimi_moonshot"
    assert mp.normalize_profile_id("") == "kimi_moonshot"
    monkeypatch.setattr(mp.settings, "CHAT_DEFAULT_MODEL_PROFILE", "gemini_google")
    assert mp.normalize_profile_id(None) == "gemini_google"


def test_kimi_accepts_kimi_code_api_key_alias(monkeypatch):
    from app.services import model_profiles as mp

    monkeypatch.setattr(mp.settings, "MOONSHOT_API_KEY", "")
    monkeypatch.setattr(mp.settings, "KIMI_CODE_API_KEY", "sk-from-code-console")
    m = mp.build_chat_model("kimi_moonshot", require_search_capable=False)
    assert m is not None


def test_model_profile_kimi_extra_body_when_search(monkeypatch):
    from app.services import model_profiles as mp

    monkeypatch.setattr(mp.settings, "MOONSHOT_API_KEY", "sk-fake")
    monkeypatch.setattr(mp.settings, "CHAT_ENABLE_GOOGLE_SEARCH", True)
    monkeypatch.setattr(mp.settings, "KIMI_DISABLE_THINKING_FOR_SEARCH", True)
    m = mp.build_chat_model(
        "kimi_moonshot",
        require_search_capable=True,
    )
    # ChatOpenAI stores OpenAI-compat extra fields for Moonshot / Kimi Code
    assert getattr(m, "extra_body", None) == mp._kimi_thinking_disabled_body()
