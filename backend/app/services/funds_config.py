"""Loader + atomic writer for ``data/config/funds.json``.

Stores the registry of Taihill-controlled funds ("Taihill Venture Series III LP",
"Newlight Fund I LP", …). A position on any entity references a fund by `id`.
File-backed rather than a SQL table so fund setup is a one-time manual edit —
no admin UI required. Mirrors the continuous_tasks.json pattern.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings


_ID_RE = re.compile(r"^[a-z0-9_]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fund(_Strict):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class FundsConfig(_Strict):
    funds: list[Fund] = Field(default_factory=list)


def _validate_ids(cfg: FundsConfig) -> None:
    seen: set[str] = set()
    for f in cfg.funds:
        if not _ID_RE.match(f.id):
            raise ValueError(
                f"fund id {f.id!r} must be snake_case [a-z0-9_]+"
            )
        if f.id in seen:
            raise ValueError(f"duplicate fund id {f.id!r}")
        seen.add(f.id)


def load_funds() -> FundsConfig:
    """Read + validate the funds config. Returns empty config if file missing."""
    p = settings.FUNDS_CONFIG_PATH
    if not p.exists():
        return FundsConfig(funds=[])
    raw = json.loads(p.read_text(encoding="utf-8"))
    try:
        cfg = FundsConfig.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"funds.json is invalid:\n{e}") from e
    _validate_ids(cfg)
    return cfg


def write_funds(data: dict[str, Any]) -> FundsConfig:
    """Validate then write atomically (tmp → os.replace).

    Returns the parsed config so callers can echo it back to clients.
    """
    cfg = FundsConfig.model_validate(data)
    _validate_ids(cfg)

    p = settings.FUNDS_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, p)
    return cfg


def upsert_fund(fund: Fund) -> FundsConfig:
    """Add or replace a fund by id. Preserves list order; appends new funds."""
    existing = load_funds()
    out: list[Fund] = []
    replaced = False
    for f in existing.funds:
        if f.id == fund.id:
            out.append(fund)
            replaced = True
        else:
            out.append(f)
    if not replaced:
        out.append(fund)
    return write_funds({"funds": [f.model_dump() for f in out]})


def delete_fund(fund_id: str) -> FundsConfig:
    """Remove a fund by id. No-op if the id isn't present."""
    existing = load_funds()
    kept = [f for f in existing.funds if f.id != fund_id]
    return write_funds({"funds": [f.model_dump() for f in kept]})
