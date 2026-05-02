"""Unit tests for the URL-rewriting logic inside ``grounded_search_json``.

These exercise the deterministic pieces — JSON-array span parsing and
the grounding-chunk attach step — without touching the network or the
Gemini API.
"""
from __future__ import annotations

import pytest

from app.services.academic.llm_client import (
    _attach_grounding_urls,
    _parse_json_array_with_spans,
    google_search_url as _google_search_url,
)


# ── span parsing ────────────────────────────────────────────────────────


def test_span_parse_two_items():
    text = '[\n  {"title": "A", "url": "x"},\n  {"title": "B", "url": "y"}\n]'
    items, start, spans = _parse_json_array_with_spans(text)
    assert [i["title"] for i in items] == ["A", "B"]
    assert start == 0
    assert len(spans) == 2
    # Each span must enclose its dict
    for (s, e), item in zip(spans, items):
        assert text[s] == "{"
        assert text[e - 1] == "}"


def test_span_parse_with_leading_prose():
    text = 'Here is the result:\n[\n  {"title": "One"}\n]\nThanks.'
    items, start, spans = _parse_json_array_with_spans(text)
    assert len(items) == 1
    assert start == text.find("[")
    s, e = spans[0]
    assert text[s] == "{"
    assert text[e - 1] == "}"


def test_span_parse_no_array():
    items, start, spans = _parse_json_array_with_spans("no json here")
    assert items == []
    assert spans == []


def test_span_parse_malformed():
    items, _, _ = _parse_json_array_with_spans('[{"broken":')
    assert items == []


# ── grounding attach ───────────────────────────────────────────────────


def _grounding(chunks, supports):
    return {"chunks": chunks, "supports": supports}


def test_attach_maps_each_item_to_its_support():
    text = '[{"title": "A"}, {"title": "B"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    # First dict is [1,16), second is [18, 32). Supports should sit inside.
    chunks = [
        {"url": "https://real-a.example/article", "title": "a", "domain": "a"},
        {"url": "https://real-b.example/article", "title": "b", "domain": "b"},
    ]
    supports = [
        {"start": 1, "end": 15, "chunk_indices": [0]},   # covers first obj
        {"start": 18, "end": 31, "chunk_indices": [1]},  # covers second obj
    ]
    _attach_grounding_urls(items, text, spans, _grounding(chunks, supports))
    assert items[0]["url"] == "https://real-a.example/article"
    assert items[0]["_url_source"] == "grounding"
    assert items[1]["url"] == "https://real-b.example/article"
    assert items[1]["_url_source"] == "grounding"


def test_attach_preserves_llm_url_keeps_chunks_as_backup():
    # Contract change (2026-05-01): we no longer blindly overwrite the LLM's
    # URL with the first chunk URL. The LLM URL is typically the more
    # specific article URL; the chunk URL is whichever source the model
    # cited and is often a coarser homepage / listing page. url_fallback
    # picks whichever one content-validates against the item title.
    text = '[{"title": "A", "url": "https://specific.example/article"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    chunks = [{"url": "https://coarse.example/listing", "title": "", "domain": ""}]
    supports = [{"start": 1, "end": len(text) - 1, "chunk_indices": [0]}]
    _attach_grounding_urls(items, text, spans, _grounding(chunks, supports))
    assert items[0]["url"] == "https://specific.example/article"
    assert items[0]["_url_source"] == "llm_with_grounding"
    assert items[0]["_llm_url"] == "https://specific.example/article"
    assert items[0]["_grounding_chunk_urls"] == ["https://coarse.example/listing"]
    assert items[0]["_all_grounding_urls"] == ["https://coarse.example/listing"]


def test_attach_uses_chunk_url_when_llm_url_absent():
    # When LLM emitted no URL, fall back to the first chunk URL.
    text = '[{"title": "A"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    chunks = [{"url": "https://real.example/article", "title": "", "domain": ""}]
    supports = [{"start": 1, "end": len(text) - 1, "chunk_indices": [0]}]
    _attach_grounding_urls(items, text, spans, _grounding(chunks, supports))
    assert items[0]["url"] == "https://real.example/article"
    assert items[0]["_url_source"] == "grounding"
    assert "_llm_url" not in items[0]
    assert items[0]["_grounding_chunk_urls"] == ["https://real.example/article"]


def test_attach_leaves_llm_url_when_no_citation_overlaps():
    # No-citation items are kept pending — the HTTP post-processor
    # (not exercised here) decides whether to validate or fall back.
    text = '[{"title": "A", "url": "https://fake.example"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    chunks = [{"url": "https://real.example", "title": "", "domain": ""}]
    supports = [{"start": len(text) - 1, "end": len(text), "chunk_indices": [0]}]
    _attach_grounding_urls(items, text, spans, _grounding(chunks, supports))
    assert items[0]["url"] == "https://fake.example"
    assert items[0]["_url_source"] == "no_citation"


def test_attach_leaves_llm_url_when_no_grounding_at_all():
    text = '[{"title": "A", "url": "https://fake"}, {"title": "B", "url": "https://fake2"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    _attach_grounding_urls(items, text, spans, _grounding([], []))
    assert items[0]["url"] == "https://fake"
    assert items[1]["url"] == "https://fake2"
    assert all(i["_url_source"] == "no_grounding" for i in items)


def test_attach_writes_source_url_for_red_flags_shape():
    # Red-flag items use `source_url` instead of `url` — the LLM URL is
    # still preferred (post-2026-05-01 contract); chunks live in
    # `_grounding_chunk_urls` for url_fallback to retry on.
    text = '[{"claim": "x", "source_url": "https://specific.example/article"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    chunks = [{"url": "https://coarse.example/listing", "title": "", "domain": ""}]
    supports = [{"start": 1, "end": len(text) - 1, "chunk_indices": [0]}]
    _attach_grounding_urls(items, text, spans, _grounding(chunks, supports))
    assert items[0]["source_url"] == "https://specific.example/article"
    assert items[0]["_grounding_chunk_urls"] == ["https://coarse.example/listing"]
    assert "url" not in items[0]


def test_attach_multiple_chunks_stores_all():
    text = '[{"title": "A"}]'
    items, _, spans = _parse_json_array_with_spans(text)
    chunks = [
        {"url": "https://one.example", "title": "", "domain": ""},
        {"url": "https://two.example", "title": "", "domain": ""},
    ]
    supports = [{"start": 1, "end": len(text) - 1, "chunk_indices": [0, 1]}]
    _attach_grounding_urls(items, text, spans, _grounding(chunks, supports))
    assert items[0]["url"] == "https://one.example"
    assert items[0]["_all_grounding_urls"] == [
        "https://one.example", "https://two.example",
    ]


# ── Google-search fallback helper ──────────────────────────────────────


def test_search_url_uses_title_first():
    u = _google_search_url({"title": "Some Article", "name": "Company"})
    assert u == "https://www.google.com/search?q=Some+Article"


def test_search_url_falls_through_anchor_fields():
    assert _google_search_url({"name": "StartCo"}) == (
        "https://www.google.com/search?q=StartCo"
    )
    assert _google_search_url({"claim": "He retracted a paper."}) == (
        "https://www.google.com/search?q=He+retracted+a+paper."
    )


def test_search_url_quotes_special_chars():
    u = _google_search_url({"title": "A & B: über 2026?"})
    assert "%26" in u  # & encoded
    assert "%C3%BCber" in u  # ü encoded


def test_search_url_empty_when_no_anchor():
    assert _google_search_url({"summary": "nope"}) == ""
