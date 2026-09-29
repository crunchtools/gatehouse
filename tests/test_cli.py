"""CLI argument handling (run_review mocked)."""

from __future__ import annotations

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
