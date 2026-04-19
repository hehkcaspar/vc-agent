"""Pydantic schemas for portfolio-side tracking sources."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Key-person ranker (news_web pre-pass) ─────────────────────────────


class KeyPersonPick(BaseModel):
    """One selected person to include as a search target."""

    name: str
    role: str = Field(
        default="",
        description="Short title (e.g. 'CTO', 'co-founder & chief scientist').",
    )
    reason: str = Field(
        default="",
        description="Why this person was picked over others (1 sentence).",
    )


class KeyPersonRankerResult(BaseModel):
    """Up-to-5 selected persons, ordered most-important first.

    The ranker is explicitly told it MAY return fewer than 5 when the
    remaining candidates don't add signal.
    """

    picks: list[KeyPersonPick] = Field(default_factory=list, max_length=5)


# ── Relevance filter (shared shape with scholar news_web) ─────────────


class CompanyNewsRelevanceItem(BaseModel):
    index: int
    relevant: bool
    reason: str
    duplicate_of: Optional[int] = None


class CompanyNewsFilterResult(BaseModel):
    items: list[CompanyNewsRelevanceItem]
