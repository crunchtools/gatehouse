"""CLI argument handling (run_review mocked)."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from gatehouse.cli import main
from gatehouse.review import REQUIRED_LOW_AGENTS


def _required_low(monkeypatch: pytest.MonkeyPatch, *argv: str) -> frozenset[str]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["gatehouse", *argv])
    review = AsyncMock(return_value=0)
    with patch("gatehouse.cli.run_review", review), pytest.raises(SystemExit):
        main()
    return review.call_args.kwargs["required_low_agents"]


def test_required_low_agents_defaults_to_bugs_and_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _required_low(monkeypatch) == REQUIRED_LOW_AGENTS


def test_required_low_agents_parses_names(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _required_low(monkeypatch, "--required-low-agents", " Bug Hunter, Test Coverage ,,")
    assert got == frozenset({"Bug Hunter", "Test Coverage"})
    assert _required_low(monkeypatch, "--required-low-agents", "") == frozenset()


def test_incremental_and_usage_json_are_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["gatehouse", "--incremental", "--usage-json", "usage.json"])
    review = AsyncMock(return_value=0)
    with patch("gatehouse.cli.run_review", review), pytest.raises(SystemExit):
        main()
    assert review.call_args.kwargs["incremental"] is True
    assert review.call_args.kwargs["usage_json"] == "usage.json"


def test_samples_flag_reaches_run_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented --samples option must actually be forwarded."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["gatehouse", "--samples", "2"])
    with (
        patch("gatehouse.cli.run_review", new_callable=AsyncMock, return_value=0) as review,
        pytest.raises(SystemExit) as exit_info,
    ):
        main()
    assert exit_info.value.code == 0
    assert review.call_args.kwargs["samples"] == 2


def test_no_samples_flag_leaves_the_count_to_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["gatehouse"])
    with (
        patch("gatehouse.cli.run_review", new_callable=AsyncMock, return_value=0) as review,
        pytest.raises(SystemExit),
    ):
        main()
    assert review.call_args.kwargs["samples"] is None
