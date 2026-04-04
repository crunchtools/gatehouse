"""Tests for output formatting."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

from gatehouse.agents import BUG_HUNTER, GENERAL
from gatehouse.output import (
    format_finding,
    format_results,
    print_summary,
    severity_rank,
    use_color,
)


def _make_finding(**overrides: Any) -> dict[str, Any]:
    """Create a finding dict with defaults."""
    base: dict[str, Any] = {
        "file": "src/app.py",
        "lineStart": 10,
        "lineEnd": 12,
        "severity": "high",
        "category": "bug",
        "description": "Test finding",
        "suggestion": "Fix it",
        "evidence": "line 10: bad()",
        "confidence": 95,
    }
    base.update(overrides)
    return base


def test_format_finding_basic() -> None:
    finding = _make_finding()
    output = format_finding(finding)
    assert "HIGH" in output
    assert "src/app.py:10-12" in output
    assert "Test finding" in output
    assert "Fix it" in output
    assert "95%" in output


def test_format_finding_single_line() -> None:
    finding = _make_finding(lineStart=5, lineEnd=5)
    output = format_finding(finding)
    assert "src/app.py:5" in output
    assert "5-" not in output


def test_format_finding_no_suggestion() -> None:
    finding = _make_finding(suggestion="")
    output = format_finding(finding)
    assert "Suggestion" not in output


def test_format_finding_no_evidence() -> None:
    finding = _make_finding(evidence="")
    output = format_finding(finding)
    assert "Evidence" not in output


def test_format_finding_critical_severity() -> None:
    finding = _make_finding(severity="critical")
    output = format_finding(finding)
    assert "CRITICAL" in output


def test_format_finding_low_severity() -> None:
    finding = _make_finding(severity="low")
    output = format_finding(finding)
    assert "LOW" in output


def test_severity_rank_ordering() -> None:
    assert severity_rank({"severity": "critical"}) < severity_rank(
        {"severity": "high"}
    )
    assert severity_rank({"severity": "high"}) < severity_rank(
        {"severity": "medium"}
    )
    assert severity_rank({"severity": "medium"}) < severity_rank(
        {"severity": "low"}
    )


def test_severity_rank_unknown() -> None:
    assert severity_rank({"severity": "unknown"}) > severity_rank(
        {"severity": "low"}
    )


def test_no_color_env() -> None:
    with patch.dict(os.environ, {"NO_COLOR": "1"}):
        assert not use_color()


def test_color_disabled_for_non_tty() -> None:
    assert not use_color()


def test_format_results_no_findings(capsys: Any) -> None:
    results: list[tuple[Any, list[dict[str, Any]]]] = [
        (BUG_HUNTER, []),
        (GENERAL, []),
    ]
    format_results(results)
    captured = capsys.readouterr()
    assert "No issues found" in captured.out


def test_format_results_with_findings(capsys: Any) -> None:
    results: list[tuple[Any, list[dict[str, Any]]]] = [
        (BUG_HUNTER, [_make_finding()]),
    ]
    format_results(results)
    captured = capsys.readouterr()
    assert "Bug Hunter" in captured.out
    assert "1 finding" in captured.out


def test_format_results_advisory_label(capsys: Any) -> None:
    results: list[tuple[Any, list[dict[str, Any]]]] = [
        (GENERAL, [_make_finding(severity="medium")]),
    ]
    format_results(results)
    captured = capsys.readouterr()
    assert "advisory" in captured.out


def test_print_summary_with_findings(capsys: Any) -> None:
    results: list[tuple[Any, list[dict[str, Any]]]] = [
        (BUG_HUNTER, [_make_finding(severity="high")]),
    ]
    print_summary(results, exit_code=1)
    captured = capsys.readouterr()
    assert "Summary" in captured.out
    assert "1 findings" in captured.out or "1 high" in captured.out


def test_print_summary_no_findings(capsys: Any) -> None:
    results: list[tuple[Any, list[dict[str, Any]]]] = [
        (BUG_HUNTER, []),
    ]
    print_summary(results, exit_code=0)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_summary_exit_zero(capsys: Any) -> None:
    results: list[tuple[Any, list[dict[str, Any]]]] = [
        (GENERAL, [_make_finding(severity="medium")]),
    ]
    print_summary(results, exit_code=0)
    captured = capsys.readouterr()
    assert "Exit: 0" in captured.out
