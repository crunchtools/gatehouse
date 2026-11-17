"""Model replies that carry more than one JSON value, or trailing prose."""

from __future__ import annotations

import json

import pytest

from gatehouse.review import MAX_QUOTE_REPAIRS, _parse_findings

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


def test_raw_quotes_in_evidence_are_repaired() -> None:
    # Code quoted from the diff with its quotes unescaped, as in issue #129.
    reply = (
        '[{"severity": "high", "confidence": 100, "description": "x", '
        '"evidence": "if name == "": raise ValueError("bad "name"")"}]'
    )
    assert _parse_findings(reply) == [
        {**HIT, "evidence": 'if name == "": raise ValueError("bad "name"")'}
    ]


def test_raw_quote_before_comma_is_repaired() -> None:
    reply = '[{"severity": "high", "confidence": 100, "description": "a = "b", c = "d""}]'
    assert _parse_findings(reply) == [{**HIT, "description": 'a = "b", c = "d"'}]


def test_unrepairable_reply_is_unfinished() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_findings('[{"severity": "high", "confidence": 100,')


def test_repair_gives_up_after_max_quote_repairs() -> None:
    quoted = " ".join(f'"w{i}"' for i in range(MAX_QUOTE_REPAIRS + 1))
    with pytest.raises(json.JSONDecodeError):
        _parse_findings(f'[{{"description": "{quoted}", "confidence": 100}}]')
