"""Tests for the stability measurement.

The Jaccard number is what the repeatability claim rests on, so it is pinned
rather than trusted: an agreement score nobody checks is a number nobody
should quote.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stability_eval.py"


# The script is not part of the installed package, so it is imported by path.
_spec = importlib.util.spec_from_file_location("stability_eval", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
evaluator = importlib.util.module_from_spec(_spec)
sys.modules["stability_eval"] = evaluator
_spec.loader.exec_module(evaluator)


def test_identical_runs_agree_completely() -> None:
    assert evaluator.jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_disjoint_runs_agree_not_at_all() -> None:
    assert evaluator.jaccard({"a"}, {"b"}) == 0.0


def test_two_empty_runs_agree() -> None:
    """Both runs found nothing, which is agreement, not a divide by zero."""
    assert evaluator.jaccard(set(), set()) == 1.0


def test_one_empty_run_against_one_finding_is_total_disagreement() -> None:
    assert evaluator.jaccard(set(), {"a"}) == 0.0


def test_partial_overlap_is_the_intersection_over_the_union() -> None:
    assert evaluator.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_pairwise_compares_every_pair_once() -> None:
    runs = [{"a"}, {"a"}, {"b"}]
    scores = evaluator.pairwise(runs)
    assert len(scores) == 3
    assert sorted(scores) == [0.0, 0.0, 1.0]


def test_pairwise_of_one_run_has_nothing_to_compare() -> None:
    assert evaluator.pairwise([{"a"}]) == []


def test_a_measurement_renders_a_row() -> None:
    row = evaluator.Measurement(
        diff="example.diff",
        runs=3,
        counts=[2, 2, 3],
        stability=0.42,
        blocking_stability=1.0,
        blocked=[True, True, True],
        verdict_agreed=True,
        always=["a"],
        sometimes=["b"],
    ).row()
    assert "example.diff" in row
    assert "2,2,3" in row
    assert "0.42" in row
    assert "1.00" in row


def test_the_report_carries_the_mean() -> None:
    results = [
        evaluator.Measurement("one.diff", 3, [1, 1, 1], 1.0, 1.0, [True] * 3, True, [], []),
        evaluator.Measurement("two.diff", 3, [1, 0, 0], 0.0, 1.0, [False] * 3, True, [], []),
    ]
    rendered = evaluator.report(results)
    assert "mean across 2 diff(s): reported 0.50, blocking 1.00" in rendered


def test_an_empty_report_says_nothing_rather_than_dividing_by_zero() -> None:
    assert evaluator.report([]).startswith("diff")


@pytest.mark.asyncio
async def test_measure_scores_runs_without_calling_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One finding seen in two of three runs: the pairs score 1, 0, 0."""
    diff = tmp_path / "one.diff"
    diff.write_text("diff --git a/a.py b/a.py\n")
    answers = [{"x"}, {"x"}, set()]

    async def fake_review(_diff: str, _key: str, _samples: int | None):
        return evaluator.Review(reported=answers.pop(0), blocking=set())

    monkeypatch.setattr(evaluator, "review_once", fake_review)
    results = await evaluator.measure([diff], 3, None, "key")

    assert len(results) == 1
    assert results[0].counts == [1, 1, 0]
    assert results[0].stability == pytest.approx(1 / 3)
    assert results[0].always == []
    assert results[0].sometimes == ["x"]


@pytest.mark.asyncio
async def test_measure_reports_a_finding_every_run_saw_as_always(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = tmp_path / "one.diff"
    diff.write_text("diff --git a/a.py b/a.py\n")

    async def fake_review(_diff: str, _key: str, _samples: int | None):
        return evaluator.Review(reported={"x"}, blocking={"x"})

    monkeypatch.setattr(evaluator, "review_once", fake_review)
    results = await evaluator.measure([diff], 3, None, "key")

    assert results[0].stability == 1.0
    assert results[0].always == ["x"]
    assert results[0].sometimes == []


@pytest.mark.asyncio
async def test_the_blocking_set_is_scored_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promise is the blocking set, so it is measured on its own."""
    diff = tmp_path / "one.diff"
    diff.write_text("diff --git a/a.py b/a.py\n")
    answers = [
        evaluator.Review(reported={"x", "y"}, blocking={"x"}),
        evaluator.Review(reported={"x"}, blocking={"x"}),
        evaluator.Review(reported={"x", "z"}, blocking={"x"}),
    ]

    async def fake_review(_diff: str, _key: str, _samples: int | None):
        return answers.pop(0)

    monkeypatch.setattr(evaluator, "review_once", fake_review)
    results = await evaluator.measure([diff], 3, None, "key")

    assert results[0].blocking_stability == 1.0
    assert results[0].stability < 1.0
    assert results[0].always == ["x"]


@pytest.mark.asyncio
async def test_a_verdict_that_flips_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing that must not happen: the same tree passing once and failing next."""
    diff = tmp_path / "one.diff"
    diff.write_text("diff --git a/a.py b/a.py\n")
    answers = [
        evaluator.Review(reported={"x"}, blocking={"x"}),
        evaluator.Review(reported={"x"}, blocking=set()),
        evaluator.Review(reported={"x"}, blocking={"x"}),
    ]

    async def fake_review(_diff: str, _key: str, _samples: int | None):
        return answers.pop(0)

    monkeypatch.setattr(evaluator, "review_once", fake_review)
    results = await evaluator.measure([diff], 3, None, "key")

    assert results[0].blocked == [True, False, True]
    assert results[0].verdict_agreed is False
    assert "FLIPPED" in evaluator.report(results)


@pytest.mark.asyncio
async def test_a_verdict_every_run_agreed_on_is_reported_as_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = tmp_path / "one.diff"
    diff.write_text("diff --git a/a.py b/a.py\n")

    async def fake_review(_diff: str, _key: str, _samples: int | None):
        return evaluator.Review(reported={"x", "y"}, blocking={"x"})

    monkeypatch.setattr(evaluator, "review_once", fake_review)
    results = await evaluator.measure([diff], 3, None, "key")

    assert results[0].verdict_agreed is True
    assert "all agreed" in evaluator.report(results)


@pytest.mark.asyncio
async def test_main_refuses_an_empty_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["stability_eval", "--corpus", str(tmp_path)])
    assert await evaluator.main() == 2
    assert "No .diff files" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_main_refuses_a_single_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One run is not a comparison, so it cannot be a stability measurement."""
    (tmp_path / "one.diff").write_text("diff --git a/a.py b/a.py\n")
    monkeypatch.setattr(sys, "argv", ["stability_eval", "--corpus", str(tmp_path), "--runs", "1"])
    assert await evaluator.main() == 2
    assert "at least 2" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_dry_run_prints_the_plan_and_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "one.diff").write_text("diff --git a/a.py b/a.py\n")

    async def fail(*_args: object) -> None:
        raise AssertionError("a dry run must not call a model")

    monkeypatch.setattr(evaluator, "review_once", fail)
    monkeypatch.setattr(sys, "argv", ["stability_eval", "--corpus", str(tmp_path), "--dry-run"])
    assert await evaluator.main() == 0
    assert "1 diff(s) x 3 runs = 3 reviews" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_main_refuses_to_run_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "one.diff").write_text("diff --git a/a.py b/a.py\n")
    monkeypatch.setattr(evaluator, "load_env_file", lambda _path: None)
    monkeypatch.setattr(evaluator, "load_api_key", lambda: "")
    monkeypatch.setattr(sys, "argv", ["stability_eval", "--corpus", str(tmp_path)])
    assert await evaluator.main() == 2
    assert "OPENROUTER_API_KEY not set" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_main_writes_the_json_it_was_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "one.diff").write_text("diff --git a/a.py b/a.py\n")
    out = tmp_path / "results.json"

    async def fake_review(_diff: str, _key: str, _samples: int | None):
        return evaluator.Review(reported={"x"}, blocking={"x"})

    monkeypatch.setattr(evaluator, "review_once", fake_review)
    monkeypatch.setattr(evaluator, "load_env_file", lambda _path: None)
    monkeypatch.setattr(evaluator, "load_api_key", lambda: "key")
    monkeypatch.setattr(
        sys,
        "argv",
        ["stability_eval", "--corpus", str(tmp_path), "--json", str(out)],
    )
    assert await evaluator.main() == 0
    written = json.loads(out.read_text())
    assert written[0]["diff"] == "one.diff"
    assert written[0]["verdict_agreed"] is True
    assert written[0]["blocked"] == [True, True, True]
