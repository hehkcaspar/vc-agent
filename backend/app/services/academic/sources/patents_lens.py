"""Layer 2 source — Lens.org patents.

SCAFFOLD — returns empty list until real Lens.org integration lands.
Tech-transfer Experience dim will note missing patent data in
`missing_data`. Tracked in the implementation plan as backlog.
"""

from __future__ import annotations

import logging
from typing import Any

from ..fact_store import record_snapshot
from ..file_utils import dossier_path, read_json, write_json

logger = logging.getLogger(__name__)

SOURCE_ID = "patents_lens"


async def run(
    scholar_id: str,
    *,
    mode: str = "incremental",
    reason: str = "",
) -> dict[str, Any]:
    path = dossier_path(scholar_id) / "patents.json"
    existing = read_json(path)
    if not existing:
        write_json(path, {"items": [], "count": 0, "scaffold": True})
    snapshot_id = await record_snapshot(
        scholar_id,
        SOURCE_ID,
        detail={"mode": mode, "reason": reason, "scaffold": True},
    )
    return {"changed": False, "snapshot_id": snapshot_id, "scaffold": True}
