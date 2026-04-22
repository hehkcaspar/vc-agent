"""Unit tests for the pagination early-break in ``_fetch_serpapi``.

The real SerpAPI call is monkeypatched with a canned page-sequence;
no network. What we verify:

- Bootstrap / unknown-ledger → fetches until the pages-list runs out
  or the ceiling.
- Incremental with a fully-known first page → breaks immediately and
  sets ``early_break=True``.
- Incremental with a partially-new first page → continues to page 2;
  breaks when page 2 is fully known.
- Known-ids set lacking ``gs-`` prefix → no false early-break.
- ``settings.SERPAPI_KEY`` missing → returns empty, early_break=False.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.academic.sources import google_scholar_papers as gsp


def _run(coro):
    return asyncio.run(coro)


class _FakeSerpApi:
    """Sequence of canned SerpAPI pages, yielded one per call.

    Each entry is the JSON-dict SerpAPI would return. Unused entries
    are fine; the fetcher stops when ``articles`` is empty or
    ``pagination.next`` is absent.
    """

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        idx = (params.get("start") or 0) // gsp._PAGE_SIZE
        if 0 <= idx < len(self.pages):
            return self.pages[idx]
        return {"articles": []}


def _art(citation_id: str, title: str = "T", year: str = "2026") -> dict:
    return {
        "title": f"{title}-{citation_id}",
        "citation_id": citation_id,
        "authors": "Song Han",
        "publication": "arXiv, 2026",
        "cited_by": {"value": 0},
        "year": year,
        "link": "",
    }


def _page(citation_ids: list[str], has_next: bool) -> dict[str, Any]:
    return {
        "articles": [_art(cid) for cid in citation_ids],
        "serpapi_pagination": {"next": "..."} if has_next else {},
    }


@pytest.fixture()
def serpapi_key(monkeypatch: pytest.MonkeyPatch):
    # Fetcher guards on settings.SERPAPI_KEY — keep the test unit-level
    # by stubbing the presence check.
    monkeypatch.setattr(gsp.settings, "SERPAPI_KEY", "test-key")


def test_bootstrap_walks_all_pages_until_empty(
    serpapi_key, monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeSerpApi([
        _page(["A", "B"], has_next=True),
        _page(["C", "D"], has_next=True),
        _page([], has_next=False),   # natural stop
    ])
    monkeypatch.setattr(gsp, "_serpapi_request", fake)

    # Bootstrap → known_gs_ids is None
    articles, early_break = _run(gsp._fetch_serpapi("test-gs-id"))
    assert len(articles) == 4
    assert early_break is False
    # 3 API calls: 2 with articles, 1 empty
    assert len(fake.calls) == 3


def test_incremental_fully_known_first_page_breaks_immediately(
    serpapi_key, monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeSerpApi([
        _page(["A", "B"], has_next=True),
        _page(["C", "D"], has_next=True),
    ])
    monkeypatch.setattr(gsp, "_serpapi_request", fake)

    known = {"gs-A", "gs-B"}
    articles, early_break = _run(
        gsp._fetch_serpapi("test-gs-id", known_gs_ids=known)
    )
    # First page fetched but pagination didn't fire beyond it.
    assert [a["citation_id"] for a in articles] == ["A", "B"]
    assert early_break is True
    assert len(fake.calls) == 1


def test_incremental_partial_new_continues_to_next_page(
    serpapi_key, monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeSerpApi([
        _page(["NEW1", "A", "B"], has_next=True),   # 1 new, 2 known
        _page(["C", "D"], has_next=True),           # all known
        _page(["E"], has_next=False),               # never reached
    ])
    monkeypatch.setattr(gsp, "_serpapi_request", fake)

    known = {"gs-A", "gs-B", "gs-C", "gs-D"}
    articles, early_break = _run(
        gsp._fetch_serpapi("test-gs-id", known_gs_ids=known)
    )
    assert [a["citation_id"] for a in articles] == [
        "NEW1", "A", "B", "C", "D",
    ]
    assert early_break is True
    assert len(fake.calls) == 2  # page 1 had new, page 2 fully known


def test_incremental_no_break_when_nothing_overlaps(
    serpapi_key, monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeSerpApi([
        _page(["X", "Y"], has_next=True),
        _page([], has_next=False),
    ])
    monkeypatch.setattr(gsp, "_serpapi_request", fake)

    # ledger has unrelated known ids — no overlap with incoming pages
    known = {"gs-Z"}
    articles, early_break = _run(
        gsp._fetch_serpapi("test-gs-id", known_gs_ids=known)
    )
    assert [a["citation_id"] for a in articles] == ["X", "Y"]
    assert early_break is False


def test_no_serpapi_key_returns_empty_no_break(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(gsp.settings, "SERPAPI_KEY", "")
    articles, early_break = _run(gsp._fetch_serpapi("test"))
    assert articles == []
    assert early_break is False


def test_known_ids_without_gs_prefix_do_not_cause_false_break(
    serpapi_key, monkeypatch: pytest.MonkeyPatch,
):
    # If the ledger has only SS ids (ss-*) and no GS ids yet,
    # page_ids (gs-*) never overlap. Bootstrap-like behaviour.
    fake = _FakeSerpApi([
        _page(["A"], has_next=True),
        _page([], has_next=False),
    ])
    monkeypatch.setattr(gsp, "_serpapi_request", fake)

    known = {"ss-42", "ss-99"}
    articles, early_break = _run(
        gsp._fetch_serpapi("test-gs-id", known_gs_ids=known)
    )
    assert [a["citation_id"] for a in articles] == ["A"]
    assert early_break is False
