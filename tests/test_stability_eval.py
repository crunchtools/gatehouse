"""Tests for the stability measurement.

The Jaccard number is what the repeatability claim rests on, so it is pinned
rather than trusted: an agreement score nobody checks is a number nobody
should quote.
"""

from __future__ import annotations

import importlib.util
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
        always=["a"],
        sometimes=["b"],
    ).row()
    assert "example.diff" in row
    assert "2,2,3" in row
    assert "0.42" in row


def test_the_report_carries_the_mean() -> None:
    results = [
        evaluator.Measurement("one.diff", 3, [1, 1, 1], 1.0, [], []),
        evaluator.Measurement("two.diff", 3, [1, 0, 0], 0.0, [], []),
    ]
    rendered = evaluator.report(results)
    assert "mean stability across 2 diff(s): 0.50" in rendered


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

    async def fake_ids(_diff: str, _key: str, _samples: int | None) -> set[str]:
        return answers.pop(0)

    monkeypatch.setattr(evaluator, "finding_ids", fake_ids)
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

    async def fake_ids(_diff: str, _key: str, _samples: int | None) -> set[str]:
        return {"x"}

    monkeypatch.setattr(evaluator, "finding_ids", fake_ids)
    results = await evaluator.measure([diff], 3, None, "key")

    assert results[0].stability == 1.0
    assert results[0].always == ["x"]
    assert results[0].sometimes == []
