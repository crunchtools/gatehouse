"""Tests for the finding ledger: parsing, matching and staleness."""

from __future__ import annotations

from typing import Any

from gatehouse.ledger import (
    DEFERRED,
    LEDGER_FILE,
    NOT_A_BUG,
    Entry,
    judgement,
    parse_ledger,
    render_entry,
    stale,
)
from gatehouse.review import _squash

REASON = "count is validated non-zero by the only caller"

ENTRY = f"""\
[[findings]]
agent = "bugs"
category = "bug"
file = "src/app.py"
evidence = "total = total / count"
status = "{NOT_A_BUG}"
reason = "{REASON}"
"""


def parse(text: str | None) -> tuple[list[Entry], list[str]]:
    return parse_ledger(text, _squash)


def finding(
    evidence: str = "total = total / count",
    *,
    category: str = "bug",
    file: str = "src/app.py",
) -> dict[str, Any]:
    return {
        "file": file,
        "category": category,
        "severity": "high",
        "evidence": evidence,
        "confidence": 95,
    }


def judged(entries: list[Entry], agent: str, item: dict[str, Any]) -> Entry | None:
    return judgement(entries, agent, item, _squash(str(item.get("evidence", ""))))


def test_no_ledger_is_no_entries_and_no_complaints() -> None:
    assert parse(None) == ([], [])
    assert parse("") == ([], [])
    assert parse("\n\n") == ([], [])


def test_an_entry_parses() -> None:
    entries, problems = parse(ENTRY)
    assert problems == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.agent == "bugs"
    assert entry.status == NOT_A_BUG
    assert entry.reason == REASON
    assert entry.evidence == "total=total/count"
    assert entry.quote == "total = total / count"


def test_an_entry_judges_the_finding_it_quotes() -> None:
    entries, _ = parse(ENTRY)
    assert judged(entries, "bugs", finding()) is not None


def test_a_judgement_survives_a_quote_of_a_different_length() -> None:
    """The next run may quote more or less of the same code."""
    entries, _ = parse(ENTRY)
    wider = finding("x = get_data()\ntotal = total / count\nuser.name.lower()")
    narrower = finding("total / count")
    assert judged(entries, "bugs", wider) is not None
    assert judged(entries, "bugs", narrower) is not None


def test_a_judgement_survives_reformatting() -> None:
    entries, _ = parse(ENTRY)
    assert judged(entries, "bugs", finding("total=total/count")) is not None


def test_a_judgement_does_not_cross_agents_categories_or_files() -> None:
    entries, _ = parse(ENTRY)
    assert judged(entries, "security", finding()) is None
    assert judged(entries, "bugs", finding(category="performance")) is None
    assert judged(entries, "bugs", finding(file="src/other.py")) is None


def test_a_judgement_does_not_cover_an_unrelated_finding_in_the_same_file() -> None:
    entries, _ = parse(ENTRY)
    assert judged(entries, "bugs", finding("user.name.lower()")) is None


def test_a_finding_with_nothing_quotable_is_never_judged() -> None:
    """An unevidenced finding cannot be matched, so it cannot be suppressed."""
    entries, _ = parse(ENTRY)
    assert judged(entries, "bugs", finding("")) is None


def test_a_deferred_entry_needs_the_issue_it_is_deferred_to() -> None:
    good = ENTRY.replace(f'status = "{NOT_A_BUG}"', f'status = "{DEFERRED}"').replace(
        REASON, "waiting on https://github.com/crunchtools/gatehouse/issues/1"
    )
    entries, problems = parse(good)
    assert problems == []
    assert entries[0].status == DEFERRED

    bad = ENTRY.replace(f'status = "{NOT_A_BUG}"', f'status = "{DEFERRED}"')
    entries, problems = parse(bad)
    assert entries == []
    assert "must link the issue" in problems[0]


def test_an_unknown_status_is_skipped_and_named() -> None:
    entries, problems = parse(ENTRY.replace(NOT_A_BUG, "wontfix"))
    assert entries == []
    assert "not one of" in problems[0]


def test_a_shrug_is_not_a_reason() -> None:
    entries, problems = parse(ENTRY.replace(REASON, "nah"))
    assert entries == []
    assert "under the 20" in problems[0]


def test_a_missing_field_is_skipped_and_named() -> None:
    entries, problems = parse(ENTRY.replace('file = "src/app.py"\n', ""))
    assert entries == []
    assert "file is missing" in problems[0]


def test_evidence_with_nothing_quotable_is_skipped() -> None:
    entries, problems = parse(ENTRY.replace("total = total / count", "   "))
    assert entries == []
    assert problems


def test_one_bad_entry_does_not_lose_the_good_ones() -> None:
    entries, problems = parse(ENTRY + ENTRY.replace(NOT_A_BUG, "wontfix"))
    assert len(entries) == 1
    assert len(problems) == 1


def test_malformed_toml_never_fails_the_review() -> None:
    entries, problems = parse("[[findings]\nagent = ")
    assert entries == []
    assert "not valid TOML" in problems[0]


def test_a_findings_key_of_the_wrong_shape_is_named() -> None:
    entries, problems = parse('findings = "none"\n')
    assert entries == []
    assert "must be a list" in problems[0]


def test_a_ledger_with_no_findings_table_is_empty() -> None:
    assert parse('[other]\nkey = "value"\n') == ([], [])


def test_an_entry_is_stale_once_its_quote_is_gone() -> None:
    entries, _ = parse(ENTRY)
    present = _squash("def divide():\n    total = total / count\n")
    assert stale(entries, present) == []
    assert stale(entries, _squash("def divide():\n    return total / max(count, 1)\n")) == entries


def test_render_entry_is_pasteable_and_round_trips() -> None:
    rendered = render_entry("bugs", finding())
    assert f'status = "{NOT_A_BUG}"' in rendered
    assert 'reason = ""' in rendered
    entries, problems = parse(rendered)
    assert entries == []  # the empty reason is what the author must fill in
    assert "reason is empty" in problems[0]

    filled = rendered.replace('reason = ""', f'reason = "{REASON}"')
    entries, problems = parse(filled)
    assert problems == []
    assert judged(entries, "bugs", finding()) is not None


def test_render_entry_escapes_a_multiline_quote() -> None:
    rendered = render_entry("bugs", finding('line one\nline "two"\tend'))
    assert "\n" not in rendered.split("evidence = ")[1].split("\n")[0][1:-1]
    filled = rendered.replace('reason = ""', f'reason = "{REASON}"')
    entries, problems = parse(filled)
    assert problems == []
    assert entries[0].quote == 'line one\nline "two"\tend'


def test_the_ledger_file_is_named_for_the_repository_root() -> None:
    assert LEDGER_FILE == "gatehouse-findings.toml"
