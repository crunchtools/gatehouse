"""Model replies that carry more than one JSON value, or trailing prose."""

from __future__ import annotations

import json

import pytest

from gatehouse.review import _parse_findings

HIT = {"severity": "high", "confidence": 100, "description": "x"}


def test_single_array() -> None:
    assert _parse_findings(json.dumps([HIT])) == [HIT]


def test_wrapped_object() -> None:
    assert _parse_findings(json.dumps({"findings": [HIT]})) == [HIT]


def test_consecutive_arrays_are_merged() -> None:
    assert _parse_findings("[]\n" + json.dumps([HIT])) == [HIT]


def test_trailing_prose_is_ignored() -> None:
    assert _parse_findings(json.dumps([HIT]) + "\n\nThat's all I found.") == [HIT]


def test_empty_reply_is_unfinished() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_findings("   ")


def test_prose_only_is_unfinished() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_findings("No issues found.")


def test_non_list_value_is_unfinished() -> None:
    with pytest.raises(TypeError):
        _parse_findings("[] 42")


def test_surrounding_whitespace() -> None:
    assert _parse_findings("\n  " + json.dumps([HIT]) + "  \n\n") == [HIT]


def test_malformed_second_value_is_trailing_text() -> None:
    assert _parse_findings(json.dumps([HIT]) + '\n[{"severity": ') == [HIT]
