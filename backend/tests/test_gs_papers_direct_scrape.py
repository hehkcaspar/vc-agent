"""Unit tests for the HTML-scrape fallback in
``google_scholar_papers._fetch_direct_scrape``.

The real ``httpx.AsyncClient.get`` is monkeypatched with canned
responses; no network. Covers the regex-parse path (title / authors /
venue / citations / year / citation_id), early-break symmetry with
SerpAPI, and natural stop conditions.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.academic.sources import google_scholar_papers as gsp


def _run(coro):
    return asyncio.run(coro)


# ── Canned HTML builders ───────────────────────────────────────────


def _row(
    *,
    citation_id: str,
    title: str,
    authors: str = "Song Han, X Y",
    venue: str = "arXiv preprint",
    year: int = 2026,
    citations: int = 7,
) -> str:
    """Render one scholar-citations table row that the regex expects."""
    href = (
        f"/citations?view_op=view_citation&hl=en&user=E0iCaa4AAAAJ"
        f"&sortby=pubdate&citation_for_view={citation_id}"
    )
    return (
        '<tr class="gsc_a_tr">'
        f'<td class="gsc_a_t">'
        f'<a href="{href}" class="gsc_a_at">{title}</a>'
        f'<div class="gs_gray">{authors}</div>'
        f'<div class="gs_gray">{venue}, {year}</div>'
        f'</td>'
        f'<td class="gsc_a_c">'
        f'<a href="#" class="gsc_a_ac gs_ibl">{citations}</a>'
        f'</td>'
        f'<td class="gsc_a_y"><span class="gsc_a_h gsc_a_hc gs_ibl">{year}</span></td>'
        '</tr>'
    )


def _page(rows: list[str]) -> str:
    return "<html><body><table>" + "".join(rows) + "</table></body></html>"


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Minimal async-context-manager httpx stub. ``pages`` is a sequence
    of responses returned one per ``get()`` call, in order.
    """

    def __init__(self, pages: list[_FakeResponse]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def get(self, url: str, *a: Any, **kw: Any) -> _FakeResponse:
        self.calls.append(url)
        idx = len(self.calls) - 1
        if idx < len(self.pages):
            return self.pages[idx]
        return _FakeResponse(status_code=200, text=_page([]))


@pytest.fixture()
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    """Install a factory that records the canned-page sequence to use."""
    state: dict[str, Any] = {"pages": []}

    def _factory(*args: Any, **kwargs: Any) -> _FakeClient:
        return _FakeClient(state["pages"])

    monkeypatch.setattr(gsp.httpx, "AsyncClient", _factory)
    return state


# ── Tests ───────────────────────────────────────────────────────────


def test_well_formed_rows_parse_all_fields(fake_httpx):
    fake_httpx["pages"] = [
        _FakeResponse(200, _page([
            _row(citation_id="CID:A", title="Paper A",
                 authors="Song Han, Y Z",
                 venue="arXiv preprint", year=2026, citations=5),
            _row(citation_id="CID:B", title="Paper B",
                 authors="X Y, Song Han",
                 venue="NeurIPS", year=2025, citations=99),
        ])),
        _FakeResponse(200, _page([])),  # natural stop
    ]
    articles, early_break = _run(gsp._fetch_direct_scrape("test-id"))
    assert len(articles) == 2
    assert early_break is False
    assert articles[0]["title"] == "Paper A"
    assert articles[0]["citation_id"] == "CID:A"
    assert articles[0]["year"] == "2026"
    assert articles[0]["cited_by"]["value"] == 5
    assert "Song Han" in articles[0]["authors"]
    assert articles[0]["publication"] == "arXiv preprint, 2026"
    assert articles[0]["link"].startswith("https://scholar.google.com/")
    assert articles[1]["citation_id"] == "CID:B"
    assert articles[1]["cited_by"]["value"] == 99


def test_row_missing_title_is_dropped(fake_httpx):
    # Second "row" has no title anchor — _TITLE_RE.search returns None,
    # the row is skipped, but the other rows are preserved.
    good = _row(citation_id="CID:A", title="Good Row")
    broken = (
        '<tr class="gsc_a_tr">'
        '<td class="gsc_a_t">NO LINK HERE</td>'
        '<td class="gsc_a_c"><a href="#" class="gsc_a_ac">3</a></td>'
        '</tr>'
    )
    fake_httpx["pages"] = [
        _FakeResponse(200, _page([good, broken])),
        _FakeResponse(200, _page([])),
    ]
    articles, _early = _run(gsp._fetch_direct_scrape("test-id"))
    assert [a["title"] for a in articles] == ["Good Row"]


def test_empty_first_page_returns_no_articles(fake_httpx):
    fake_httpx["pages"] = [_FakeResponse(200, _page([]))]
    articles, early_break = _run(gsp._fetch_direct_scrape("test-id"))
    assert articles == []
    assert early_break is False


def test_http_429_stops_gracefully_returning_what_we_had(fake_httpx):
    fake_httpx["pages"] = [
        _FakeResponse(200, _page([
            _row(citation_id="A", title="Got this"),
        ])),
        _FakeResponse(429, ""),  # rate-limited on page 2
        _FakeResponse(200, _page([_row(citation_id="C", title="Never")])),
    ]
    articles, early_break = _run(gsp._fetch_direct_scrape("test-id"))
    # Page 1 scraped; page 2's non-200 broke the loop; page 3 never
    # fired.
    assert [a["title"] for a in articles] == ["Got this"]
    assert early_break is False


def test_incremental_early_break_matches_serpapi_semantics(fake_httpx):
    fake_httpx["pages"] = [
        _FakeResponse(200, _page([
            _row(citation_id="KNOWN1", title="Already seen"),
            _row(citation_id="KNOWN2", title="Also known"),
        ])),
        _FakeResponse(200, _page([
            _row(citation_id="NEW1", title="Never reached"),
        ])),
    ]
    known = {"gs-KNOWN1", "gs-KNOWN2"}
    articles, early_break = _run(
        gsp._fetch_direct_scrape("test-id", known_gs_ids=known)
    )
    assert [a["citation_id"] for a in articles] == ["KNOWN1", "KNOWN2"]
    assert early_break is True


def test_partial_overlap_continues_to_next_page(fake_httpx):
    fake_httpx["pages"] = [
        _FakeResponse(200, _page([
            _row(citation_id="NEW", title="New paper"),
            _row(citation_id="KNOWN", title="Old one"),
        ])),
        _FakeResponse(200, _page([
            _row(citation_id="KNOWN2", title="All known"),
        ])),
        _FakeResponse(200, _page([])),
    ]
    known = {"gs-KNOWN", "gs-KNOWN2"}
    articles, early_break = _run(
        gsp._fetch_direct_scrape("test-id", known_gs_ids=known)
    )
    # Page 1 mixed → continue; page 2 fully known → early-break.
    assert [a["citation_id"] for a in articles] == [
        "NEW", "KNOWN", "KNOWN2",
    ]
    assert early_break is True
