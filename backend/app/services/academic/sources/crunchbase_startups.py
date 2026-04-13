"""Layer 2 source — Crunchbase funding / revenue data.

SCAFFOLD — ships disabled in continuous_tasks.json. Real integration
is backlog. Tech-transfer Experience falls back to news_web grounded
search for startup traction until this lands.
"""

from __future__ import annotations

from typing import Any

from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json

SOURCE_ID = "crunchbase_startups"


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    path = dossier_path(scholar_id) / "startups.json"
    if not read_json(path):
        write_json(path, {"items": [], "count": 0, "scaffold": True})
    snapshot_id = await record_snapshot(
        scholar_id,
        SOURCE_ID,
        detail={"mode": mode, "reason": reason, "scaffold": True},
    )
    return {"changed": False, "snapshot_id": snapshot_id, "scaffold": True}
