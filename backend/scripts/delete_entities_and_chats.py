"""
Manually delete entities and/or specific chat sessions from the backend.

Usage examples (from `backend/`, with venv):
  ..\\venv\\Scripts\\python.exe scripts\\delete_entities_and_chats.py --chat-entity-id <entity_id> --chat-id <session_id>
  ..\\venv\\Scripts\\python.exe scripts\\delete_entities_and_chats.py --entity-id <entity_id_1> --entity-id <entity_id_2> --yes
  ..\\venv\\Scripts\\python.exe scripts\\delete_entities_and_chats.py --entity-id <entity_id> --dry-run
  ..\\venv\\Scripts\\python.exe scripts\\delete_entities_and_chats.py --test-entities --dry-run
  ..\\venv\\Scripts\\python.exe scripts\\delete_entities_and_chats.py --test-entities --yes
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
_TESTS_SUPPORT = _BACKEND_ROOT / "tests" / "support"
if str(_TESTS_SUPPORT) not in sys.path:
    sys.path.insert(0, str(_TESTS_SUPPORT))
os.chdir(_BACKEND_ROOT)


def _db_file_from_settings() -> Path:
    from app.config import settings

    url = settings.database_url_sync
    if not url.startswith("sqlite:///"):
        print("This helper currently supports sqlite:/// only.", file=sys.stderr)
        sys.exit(1)
    return Path(url.removeprefix("sqlite:///")).resolve()


def _data_root_from_settings() -> Path:
    from app.config import settings

    return Path(settings.DATA_ROOT).resolve()


def _safe_delete(path: Path, data_root: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not str(resolved).startswith(str(data_root.resolve())):
        return False
    if resolved.is_file():
        resolved.unlink(missing_ok=True)
        return True
    if resolved.is_dir():
        shutil.rmtree(resolved, ignore_errors=True)
        return True
    return False


def _iter_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


@dataclass
class DeleteSummary:
    deleted_entities: list[str] = field(default_factory=list)
    missing_entities: list[str] = field(default_factory=list)
    deleted_sessions: list[str] = field(default_factory=list)
    missing_sessions: list[str] = field(default_factory=list)
    skipped_sessions_wrong_entity: list[str] = field(default_factory=list)
    deleted_jobs: int = 0
    deleted_messages: int = 0
    deleted_resources: int = 0
    deleted_artifacts: int = 0
    deleted_paths: int = 0


def _delete_session(
    con: sqlite3.Connection,
    session_id: str,
    *,
    expected_entity_id: str | None,
    dry_run: bool,
    summary: DeleteSummary,
) -> None:
    cur = con.cursor()
    row = cur.execute(
        "select id, entity_id from conversation_sessions where id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        summary.missing_sessions.append(session_id)
        return
    found_entity_id = row[1]
    if expected_entity_id and found_entity_id != expected_entity_id:
        summary.skipped_sessions_wrong_entity.append(session_id)
        return

    jobs_count = cur.execute(
        "select count(*) from chat_completion_jobs where session_id = ?",
        (session_id,),
    ).fetchone()[0]
    msgs_count = cur.execute(
        "select count(*) from conversation_messages where session_id = ?",
        (session_id,),
    ).fetchone()[0]

    if not dry_run:
        cur.execute("delete from chat_completion_jobs where session_id = ?", (session_id,))
        cur.execute("delete from conversation_messages where session_id = ?", (session_id,))
        cur.execute("delete from conversation_sessions where id = ?", (session_id,))

    summary.deleted_jobs += jobs_count
    summary.deleted_messages += msgs_count
    summary.deleted_sessions.append(session_id)


def _delete_entity(
    con: sqlite3.Connection,
    entity_id: str,
    *,
    data_root: Path,
    dry_run: bool,
    summary: DeleteSummary,
) -> None:
    cur = con.cursor()
    entity_row = cur.execute("select id from entities where id = ?", (entity_id,)).fetchone()
    if not entity_row:
        summary.missing_entities.append(entity_id)
        return

    session_ids = [
        r[0]
        for r in cur.execute(
            "select id from conversation_sessions where entity_id = ?",
            (entity_id,),
        ).fetchall()
    ]
    for session_id in session_ids:
        _delete_session(
            con,
            session_id,
            expected_entity_id=entity_id,
            dry_run=dry_run,
            summary=summary,
        )

    jobs_by_entity = cur.execute(
        "select count(*) from chat_completion_jobs where entity_id = ?",
        (entity_id,),
    ).fetchone()[0]
    if not dry_run:
        cur.execute("delete from chat_completion_jobs where entity_id = ?", (entity_id,))
    summary.deleted_jobs += jobs_by_entity

    resources = cur.execute(
        "select id, relative_path from resources where entity_id = ?",
        (entity_id,),
    ).fetchall()
    artifacts = cur.execute(
        "select id, relative_path from artifacts where entity_id = ?",
        (entity_id,),
    ).fetchall()

    if not dry_run:
        for _, relative_path in resources:
            if relative_path:
                if _safe_delete(data_root / relative_path, data_root):
                    summary.deleted_paths += 1
        for _, relative_path in artifacts:
            if relative_path:
                if _safe_delete(data_root / relative_path, data_root):
                    summary.deleted_paths += 1

    summary.deleted_resources += len(resources)
    summary.deleted_artifacts += len(artifacts)

    if not dry_run:
        cur.execute("delete from resources where entity_id = ?", (entity_id,))
        cur.execute("delete from artifacts where entity_id = ?", (entity_id,))
        cur.execute("delete from entities where id = ?", (entity_id,))
        # Remove the whole entity folder (covers nested artifact/resource files).
        if _safe_delete(data_root / "entities" / entity_id, data_root):
            summary.deleted_paths += 1

    summary.deleted_entities.append(entity_id)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Delete specific entities and/or chat sessions from SQLite backend data."
    )
    ap.add_argument(
        "--entity-id",
        action="append",
        default=[],
        help="Entity ID to delete entirely. Repeat for multiple IDs.",
    )
    ap.add_argument(
        "--entity-name",
        action="append",
        default=[],
        help="Delete entities by exact name match. Repeat for multiple names.",
    )
    ap.add_argument(
        "--website-contains",
        action="append",
        default=[],
        help="Delete entities where website contains this text (case-insensitive). Repeatable.",
    )
    ap.add_argument(
        "--test-entities",
        action="store_true",
        help=(
            "Delete entities matched by tests/support/entity_test_catalog.py "
            "(exact test names, 'E2E LLM ' prefix, or website containing '.test')."
        ),
    )
    ap.add_argument(
        "--chat-entity-id",
        help="Entity ID that owns chat sessions passed via --chat-id.",
    )
    ap.add_argument(
        "--chat-id",
        action="append",
        default=[],
        help="Chat session ID to delete under --chat-entity-id. Repeat for multiple IDs.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without applying changes.",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation.",
    )
    args = ap.parse_args()

    entity_ids = _iter_unique(args.entity_id)
    chat_ids = _iter_unique(args.chat_id)
    chat_entity_id = args.chat_entity_id

    name_filters = _iter_unique(args.entity_name)
    website_filters = [v.strip().lower() for v in _iter_unique(args.website_contains) if v.strip()]
    test_entities_mode = bool(args.test_entities)

    if (
        not entity_ids
        and not chat_ids
        and not name_filters
        and not website_filters
        and not test_entities_mode
    ):
        ap.error(
            "Provide --entity-id and/or --chat-id, or use --entity-name/--website-contains/--test-entities."
        )
    if chat_ids and not chat_entity_id:
        ap.error("--chat-entity-id is required when using --chat-id.")

    db_path = _db_file_from_settings()
    data_root = _data_root_from_settings()
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    if test_entities_mode:
        from entity_test_catalog import is_test_entity

    print("Delete plan:")
    print(f"  DB: {db_path}")
    print(f"  Data root: {data_root}")
    print(f"  Entities to delete: {len(entity_ids)}")
    print(f"  Chat sessions to delete: {len(chat_ids)}")
    if test_entities_mode:
        print("  Mode: --test-entities (catalog + .test websites)")
    if args.dry_run:
        print("  Mode: DRY-RUN (no changes)")

    if not args.yes and not args.dry_run:
        confirm = input("Type DELETE to continue: ").strip()
        if confirm != "DELETE":
            print("Aborted.")
            return

    con = sqlite3.connect(db_path)
    summary = DeleteSummary()
    try:
        if name_filters or website_filters or test_entities_mode:
            cur = con.cursor()
            rows = cur.execute(
                "select id, name, coalesce(website, '') from entities"
            ).fetchall()
            for entity_id, name, website in rows:
                by_name = bool(name_filters and name in name_filters)
                by_site = bool(
                    website_filters
                    and any(frag in website.lower() for frag in website_filters)
                )
                by_catalog = bool(
                    test_entities_mode and is_test_entity(name, website or None)
                )
                if by_name or by_site or by_catalog:
                    entity_ids.append(entity_id)
            entity_ids = _iter_unique(entity_ids)

        for session_id in chat_ids:
            _delete_session(
                con,
                session_id,
                expected_entity_id=chat_entity_id,
                dry_run=args.dry_run,
                summary=summary,
            )
        for entity_id in entity_ids:
            _delete_entity(
                con,
                entity_id,
                data_root=data_root,
                dry_run=args.dry_run,
                summary=summary,
            )
        if args.dry_run:
            con.rollback()
        else:
            con.commit()
    finally:
        con.close()

    print("\nResult:")
    print(f"  Deleted entities: {len(summary.deleted_entities)}")
    print(f"  Missing entities: {len(summary.missing_entities)}")
    print(f"  Deleted sessions: {len(summary.deleted_sessions)}")
    print(f"  Missing sessions: {len(summary.missing_sessions)}")
    print(f"  Skipped sessions (wrong entity): {len(summary.skipped_sessions_wrong_entity)}")
    print(f"  Deleted jobs: {summary.deleted_jobs}")
    print(f"  Deleted messages: {summary.deleted_messages}")
    print(f"  Deleted resources: {summary.deleted_resources}")
    print(f"  Deleted artifacts: {summary.deleted_artifacts}")
    print(f"  Deleted filesystem paths: {summary.deleted_paths}")

    if summary.missing_entities:
        print("  Missing entity IDs:", ", ".join(summary.missing_entities))
    if summary.missing_sessions:
        print("  Missing session IDs:", ", ".join(summary.missing_sessions))
    if summary.skipped_sessions_wrong_entity:
        print(
            "  Skipped session IDs (entity mismatch):",
            ", ".join(summary.skipped_sessions_wrong_entity),
        )


if __name__ == "__main__":
    main()
