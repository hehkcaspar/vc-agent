"""
Offline reset: delete the SQLite portfolio DB and recreate empty tables from ORM.

Stop the API (uvicorn) first so the file is not locked.

Usage (from repo root, with venv):
  cd backend
  ..\\venv\\Scripts\\python.exe scripts\\reset_sqlite_db.py --yes
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
os.chdir(_BACKEND_ROOT)


def _db_file_from_settings() -> Path:
    from app.config import settings

    u = settings.database_url_sync
    if not u.startswith("sqlite:///"):
        print("reset_sqlite_db only supports sqlite:/// URLs.", file=sys.stderr)
        sys.exit(1)
    return Path(u.removeprefix("sqlite:///")).resolve()


def _remove_sqlite_files(db_path: Path) -> None:
    paths = [
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ]
    for p in paths:
        if p.is_file():
            p.unlink()
            print(f"Removed {p}")


async def _run(*, yes: bool) -> None:
    db_path = _db_file_from_settings()
    if not yes:
        print(f"This will delete:\n  {db_path}\n  (+ -wal / -shm if present)")
        ans = input("Type YES to continue: ").strip()
        if ans != "YES":
            print("Aborted.")
            return

    # Import after path is OK (run from backend/)
    from app.academic_database import academic_engine, academic_sync_engine, init_academic_db
    from app.database import engine, sync_engine, init_db

    await engine.dispose()
    sync_engine.dispose()
    await academic_engine.dispose()
    academic_sync_engine.dispose()

    if db_path.exists() or Path(str(db_path) + "-wal").is_file():
        try:
            _remove_sqlite_files(db_path)
        except OSError as e:
            print(
                f"Could not delete database (is the API still running?): {e}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print(f"No existing file at {db_path} (will create fresh).")

    # Also reset academic DB
    from app.config import settings as _s
    academic_db_path = Path(_s.academic_database_url_sync.removeprefix("sqlite:///")).resolve()
    if academic_db_path.exists() or Path(str(academic_db_path) + "-wal").is_file():
        _remove_sqlite_files(academic_db_path)
    print(f"Creating fresh academic schema: {academic_db_path}")

    await init_db()
    print(f"Created fresh portfolio schema: {db_path}")
    await init_academic_db()
    print(f"Created fresh academic schema: {academic_db_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete vc_portfolio SQLite DB and recreate tables.")
    ap.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation (non-interactive).",
    )
    args = ap.parse_args()
    asyncio.run(_run(yes=args.yes))


if __name__ == "__main__":
    main()
