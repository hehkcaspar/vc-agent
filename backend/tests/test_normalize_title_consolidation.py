"""Smoke test: all three ``_normalize_title`` callers share ONE
implementation.

Three modules previously shipped their own (identical) copy of the
paper-matching normalizer. A silent divergence would break tombstone
matches against live paper rows. This test asserts they now all
resolve to the same function object in ``papers_merge``.
"""
from __future__ import annotations

from app.services.academic import destinations, papers_merge, tombstones


def test_all_three_are_the_same_function_object():
    canonical = papers_merge._normalize_title
    assert destinations._normalize_title is canonical
    assert tombstones._normalize is canonical


def test_canonical_behavior_consistent():
    # Same input must produce the same key when called via any of
    # the three public call sites.
    inputs = [
        "MCUNet: Tiny Deep Learning",
        "  MCUNET:   TINY   deep    learning  ",
        "mcunet: tiny deep learning!",
        "  EIE:   Efficient Inference Engine on Compressed DNN  ",
        "",
        None,  # falsy input path
    ]
    for s in inputs:
        a = papers_merge._normalize_title(s or "")
        b = destinations._normalize_title(s or "")
        c = tombstones._normalize(s or "")
        assert a == b == c, f"divergence for {s!r}: {a!r} vs {b!r} vs {c!r}"


def test_canonical_strips_trailing_punctuation_and_lowercases():
    assert papers_merge._normalize_title(
        "  Hello,  World!? (test) "
    ) == "hello, world!? (test"
    # (Trailing ")" is in the strip set; leading whitespace + case
    # collapsed; comma/bang are INTERIOR so they survive.)


def test_empty_and_whitespace_only():
    assert papers_merge._normalize_title("") == ""
    assert papers_merge._normalize_title("   ") == ""
    assert papers_merge._normalize_title("   !!!   ") == ""
