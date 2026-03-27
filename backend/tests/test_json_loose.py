"""json_loose: tolerate trailing prose after JSON."""

import json

import pytest

from app.services.json_loose import parse_json_loose


def test_parse_json_loose_extra_data_after_object():
    payload = '{"one_liner": "x", "summary": "y", "languages": [], "document_kind": "memo", "primary_topics": [], "key_entities_or_parties": [], "approx_length_signal": "short", "full_text_recommended": {"value": true, "reason": "r"}, "skim_metadata_reliability": "high", "caveats": []}'
    text = payload + "\n\nHere is a summary for the user:\n- Point one\n"
    out = parse_json_loose(text)
    assert out["one_liner"] == "x"
    assert out["document_kind"] == "memo"


def test_parse_json_loose_fenced_then_junk():
    text = '```json\n{"a": 1}\n```\n\nmore text'
    assert parse_json_loose(text) == {"a": 1}


def test_parse_json_loose_array_of_object():
    text = '[{"k": "v"}]\ntrailing'
    assert parse_json_loose(text) == {"k": "v"}


def test_parse_json_loose_empty_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_loose("   ")
