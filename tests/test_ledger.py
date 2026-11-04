"""Tests for the finding ledger: parsing, matching and staleness."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from gatehouse.agents import BUG_HUNTER
from gatehouse.ledger import (
    DEFERRED,
    LEDGER_FILE,
    NOT_A_BUG,
    Entry,
    entries_added,
    entry_line,
    judgement,
    parse_ledger,
    render_entry,
    stale,
    suppression_finding,
)
from gatehouse.llm import Completion
from gatehouse.review import _squash, run_review

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


LEDGERED = f"""\
[[findings]]
agent = "bugs"
category = "bug"
file = "src/app.py"
evidence = "total = total / count"
status = "{NOT_A_BUG}"
reason = "{REASON}"
"""

DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,4 @@
-my_var = 1
+myVar = 1
 x = get_data()
+user.name.lower()
+total = total / count
"""


def _model_finding(evidence: str = "total = total / count") -> dict[str, Any]:
    return {
        "file": "src/app.py",
        "lineStart": 5,
        "lineEnd": 5,
        "severity": "high",
        "category": "bug",
        "description": "divides by a count that can be zero",
        "suggestion": "guard it",
        "evidence": evidence,
        "confidence": 95,
    }


def _reply(findings: list[dict[str, Any]]) -> Completion:
    return Completion(json.dumps(findings))


@pytest.mark.asyncio
async def test_a_judged_finding_is_not_raised_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of the ledger: answer a finding once and the gate passes next run."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / LEDGER_FILE).write_text(LEDGERED)

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=_reply([_model_finding()]),
        ),
        patch("gatehouse.review.format_results") as formatted,
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 0
    assert formatted.call_args.args[0] == [(BUG_HUNTER, [])]
    assert "Skipped 1 finding(s) already judged" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_without_the_ledger_the_same_finding_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=_reply([_model_finding()]),
        ),
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 1


@pytest.mark.asyncio
async def test_a_judgement_does_not_cover_a_different_finding_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / LEDGER_FILE).write_text(LEDGERED)
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=_reply([_model_finding("user.name.lower()")]),
        ),
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 1


@pytest.mark.asyncio
async def test_a_run_prints_an_entry_to_paste_for_each_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=_reply([_model_finding()]),
        ),
    ):
        await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    out = capsys.readouterr().out
    assert "[[findings]]" in out
    assert 'agent = "bugs"' in out
    assert 'reason = ""' in out


@pytest.mark.asyncio
async def test_a_stale_entry_is_reported_for_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A suppression must not outlive the code it covered."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / LEDGER_FILE).write_text(
        LEDGERED.replace("total = total / count", "gone = long_gone()")
    )
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, return_value=_reply([])),
    ):
        await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    err = capsys.readouterr().err
    assert "Stale gatehouse-findings.toml entry" in err
    assert "src/app.py" in err


@pytest.mark.asyncio
async def test_a_broken_ledger_is_named_and_the_review_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / LEDGER_FILE).write_text("[[findings]\nagent = ")
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=_reply([_model_finding()]),
        ),
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 1  # the finding it would have judged still stands
    assert "not valid TOML" in capsys.readouterr().err


BASE_ONLY = LEDGERED.replace("total = total / count", "already_judged_on_base = 1").replace(
    REASON, "judged on the base branch when it was merged"
)


def test_entries_added_are_the_ones_the_base_does_not_have() -> None:
    head, _ = parse(BASE_ONLY + LEDGERED)
    base, _ = parse(BASE_ONLY)
    added = entries_added(head, base)
    assert [e.evidence for e in added] == ["total=total/count"]


def test_nothing_is_added_when_the_ledgers_match() -> None:
    head, _ = parse(LEDGERED)
    assert entries_added(head, head) == []


def test_entry_line_points_at_the_evidence_line() -> None:
    entries, _ = parse(BASE_ONLY + LEDGERED)
    text = BASE_ONLY + LEDGERED
    assert entry_line(text, entries[0]) == 5
    assert entry_line(text, entries[1]) == 12


def test_entry_line_is_zero_when_the_quote_is_not_in_the_text() -> None:
    entries, _ = parse(LEDGERED)
    assert entry_line("nothing here\n", entries[0]) == 0


def test_a_suppression_is_a_medium_finding_triage_demands_an_answer_to() -> None:
    entries, _ = parse(LEDGERED)
    item = suppression_finding(entries[0], 5)
    assert item["severity"] == "medium"
    assert item["file"] == LEDGER_FILE
    assert item["lineStart"] == 5
    assert "silences a bugs finding in src/app.py" in item["description"]
    assert REASON in item["description"]


@pytest.mark.asyncio
async def test_a_new_entry_is_posted_for_a_maintainer_to_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suppression is the one way to silence this gate, so it must be answered."""
    monkeypatch.chdir(tmp_path)
    head_text = BASE_ONLY + LEDGERED
    ledger_diff = "".join(f"+{line}\n" for line in head_text.splitlines())
    diff = (
        "diff --git a/gatehouse-findings.toml b/gatehouse-findings.toml\n"
        "--- a/gatehouse-findings.toml\n"
        "+++ b/gatehouse-findings.toml\n"
        f"@@ -0,0 +1,{len(head_text.splitlines())} @@\n"
        f"{ledger_diff}"
    )

    def fake_fetch(_repo: str, ref: str, path: str, _token: str) -> str | None:
        if path != LEDGER_FILE:
            return None
        return BASE_ONLY if ref == "base-sha" else head_text

    monkeypatch.setenv("GATEHOUSE_CONTEXT_REPO", "crunchtools/gatehouse")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REF", "base-sha")
    monkeypatch.setenv("GATEHOUSE_HEAD_SHA", "head-sha")
    monkeypatch.setenv("GATEHOUSE_HEAD_REPO", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    with (
        patch("gatehouse.review.fetch_repo_file", side_effect=fake_fetch),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.load_constitution", return_value=None),
        patch("gatehouse.review.fetch_answered_threads", return_value=[]),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, return_value=_reply([])),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
    ):
        await run_review(stdin_diff=diff, agent_slugs=["bugs"], api_key="k", comment=True)

    posted = [f for _, findings in post.call_args.args[0] for f in findings]
    assert len(posted) == 1
    assert posted[0]["file"] == LEDGER_FILE
    assert "silences a bugs finding" in posted[0]["description"]


@pytest.mark.asyncio
async def test_an_answered_suppression_is_not_asked_about_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-raise window already dedupes an answered thread; reuse it."""
    monkeypatch.chdir(tmp_path)
    head_text = BASE_ONLY + LEDGERED
    diff = (
        "diff --git a/gatehouse-findings.toml b/gatehouse-findings.toml\n"
        "--- a/gatehouse-findings.toml\n"
        "+++ b/gatehouse-findings.toml\n"
        f"@@ -0,0 +1,{len(head_text.splitlines())} @@\n"
        + "".join(f"+{line}\n" for line in head_text.splitlines())
    )

    def fake_fetch(_repo: str, ref: str, path: str, _token: str) -> str | None:
        if path != LEDGER_FILE:
            return None
        return BASE_ONLY if ref == "base-sha" else head_text

    monkeypatch.setenv("GATEHOUSE_CONTEXT_REPO", "crunchtools/gatehouse")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REF", "base-sha")
    monkeypatch.setenv("GATEHOUSE_HEAD_SHA", "head-sha")
    monkeypatch.setenv("GATEHOUSE_HEAD_REPO", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    with (
        patch("gatehouse.review.fetch_repo_file", side_effect=fake_fetch),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.load_constitution", return_value=None),
        patch(
            "gatehouse.review.fetch_answered_threads",
            return_value=[("Ledger", LEDGER_FILE, 12)],
        ),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, return_value=_reply([])),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
    ):
        await run_review(stdin_diff=diff, agent_slugs=["bugs"], api_key="k", comment=True)

    posted = [f for _, findings in post.call_args.args[0] for f in findings]
    assert posted == []
    assert post.call_args.kwargs["reraised"] == 1
