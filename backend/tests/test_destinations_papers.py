"""Unit tests for destination autonomy — specifically the papers
``accept_into`` policy.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.academic import destinations


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def scholar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    sid = "scholar_test"
    monkeypatch.setattr(
        "app.services.academic.destinations.dossier_path",
        lambda s: tmp_path / s,
    )
    (tmp_path / sid).mkdir()
    return sid


def _papers_file(scholar_dir: Path, sid: str) -> Path:
    return scholar_dir / sid / "papers.json"


def test_papers_accept_adds_stub_when_absent(scholar: str, tmp_path: Path):
    item = {
        "title": "Once for All: Train One Network",
        "abstract": "Specialize one network for edge deployment.",
        "inventors": ["Han Cai", "Song Han"],
        "grant_date": "2020-04-15",
        "url": "https://example.com/paper",
    }
    result = _run(destinations.accept_into(
        "papers", scholar, item, source_category="patents",
    ))
    assert result["accepted"] is True
    assert result["action"] == "added_stub"
    assert result["stored_id"].startswith("stub-")

    path = _papers_file(tmp_path, scholar)
    data = json.loads(path.read_text())
    assert data["count"] == 1
    stub = data["items"][0]
    assert stub["title"] == item["title"]
    assert stub["_stub"] is True
    assert stub["_origin"] == "routed_from:patents"
    assert stub["year"] == 2020
    assert {a["name"] for a in stub["authors"]} == {"Han Cai", "Song Han"}


def test_papers_accept_noops_when_already_tracked(
    scholar: str, tmp_path: Path,
):
    papers_path = _papers_file(tmp_path, scholar)
    papers_path.write_text(json.dumps({
        "items": [
            {
                "id": "ss-12345",
                "title": "Once for All: Train One Network",
                "authors": [{"name": "Song Han"}],
                "year": 2020,
                "citations": 1200,
            },
        ],
        "count": 1,
    }))

    item = {"title": "  once for all: train one network  ",
            "abstract": "stub", "inventors": ["Song Han"]}
    result = _run(destinations.accept_into(
        "papers", scholar, item, source_category="patents",
    ))
    assert result["accepted"] is False
    assert result["action"] == "already_tracked"
    assert result["stored_id"] == "ss-12345"

    data = json.loads(papers_path.read_text())
    assert data["count"] == 1
    assert data["items"][0]["id"] == "ss-12345"


def test_papers_accept_rejects_when_title_missing(scholar: str):
    item = {"abstract": "no title here"}
    result = _run(destinations.accept_into(
        "papers", scholar, item, source_category="patents",
    ))
    assert result["accepted"] is False
    assert result["action"] == "shape_invalid"


def test_papers_accept_dedups_on_repeated_calls(scholar: str, tmp_path: Path):
    item = {"title": "MCUNet: Tiny Deep Learning",
            "abstract": "TinyML method.", "inventors": ["Song Han"]}
    r1 = _run(destinations.accept_into(
        "papers", scholar, item, source_category="patents",
    ))
    assert r1["accepted"] is True
    r2 = _run(destinations.accept_into(
        "papers", scholar, item, source_category="patents",
    ))
    assert r2["accepted"] is False
    assert r2["action"] == "already_tracked"

    data = json.loads(_papers_file(tmp_path, scholar).read_text())
    assert data["count"] == 1


def test_unknown_destination_returns_not_implemented(scholar: str):
    item = {"title": "Some item"}
    result = _run(destinations.accept_into(
        "news", scholar, item, source_category="patents",
    ))
    assert result["accepted"] is False
    assert result["action"] == "not_implemented"
