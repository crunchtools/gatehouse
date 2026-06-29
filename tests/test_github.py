"""Tests for GitHub PR review integration."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest  # noqa: TC002

if TYPE_CHECKING:
    from pathlib import Path

from gatehouse.agents import BUG_HUNTER, GENERAL, SECURITY_SCAN
from gatehouse.github import (
    _format_comment_body,
    detect_pr_context,
    format_review_body,
    post_pr_review,
)

SAMPLE_FINDING = {
    "file": "src/app.py",
    "lineStart": 10,
    "lineEnd": 12,
    "severity": "high",
    "category": "bug",
    "description": "Null reference on user input",
    "suggestion": "Add null check",
    "evidence": "Line 10: user.name.lower()",
    "confidence": 95,
}


def test_detect_pr_context_from_github_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/42/merge")
    result = detect_pr_context()
    assert result == ("crunchtools/gatehouse", 42)


def test_detect_pr_context_from_event_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"pull_request": {"number": 99}}))
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.delenv("GITHUB_REF", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    result = detect_pr_context()
    assert result == ("crunchtools/gatehouse", 99)


def test_detect_pr_context_missing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert detect_pr_context() is None


def test_detect_pr_context_no_pr_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert detect_pr_context() is None


def test_format_comment_body() -> None:
    body = _format_comment_body("Bug Hunter", SAMPLE_FINDING)
    assert "**HIGH**" in body
    assert "Bug Hunter" in body
    assert "Null reference" in body
    assert "Add null check" in body
    assert "user.name.lower()" in body


def test_format_comment_body_no_suggestion() -> None:
    finding = {**SAMPLE_FINDING, "suggestion": "", "evidence": ""}
    body = _format_comment_body("Bug Hunter", finding)
    assert "Suggestion" not in body
    assert "Evidence" not in body


def test_format_review_body_counts() -> None:
    results = [
        (BUG_HUNTER, [SAMPLE_FINDING]),
        (SECURITY_SCAN, [{**SAMPLE_FINDING, "severity": "critical"}]),
        (GENERAL, []),
    ]
    body = format_review_body(results)
    assert "Gatehouse found 2 issues" in body
    assert "1 critical" in body
    assert "1 high" in body


def test_format_review_body_no_findings() -> None:
    results = [(BUG_HUNTER, []), (GENERAL, [])]
    assert format_review_body(results) == "Gatehouse found no issues."


def test_post_pr_review_constructs_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/7/merge")

    results = [(BUG_HUNTER, [SAMPLE_FINDING])]

    with patch("gatehouse.github.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        success = post_pr_review(results, has_blocking=True)

    assert success is True
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    payload = json.loads(call_kwargs.kwargs.get("input", call_kwargs[1].get("input", "")))
    assert payload["event"] == "REQUEST_CHANGES"
    assert len(payload["comments"]) == 1
    assert payload["comments"][0]["path"] == "src/app.py"
    assert payload["comments"][0]["line"] == 10


def test_post_pr_review_comment_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/7/merge")

    results = [(GENERAL, [SAMPLE_FINDING])]

    with patch("gatehouse.github.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        success = post_pr_review(results, has_blocking=False)

    assert success is True
    payload = json.loads(mock_run.call_args.kwargs.get(
        "input", mock_run.call_args[1].get("input", "")
    ))
    assert payload["event"] == "COMMENT"


def test_post_pr_review_no_pr_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    results = [(BUG_HUNTER, [SAMPLE_FINDING])]
    assert post_pr_review(results, has_blocking=True) is False


def test_post_pr_review_gh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/7/merge")

    results = [(BUG_HUNTER, [SAMPLE_FINDING])]

    with patch("gatehouse.github.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="auth required"
        )
        assert post_pr_review(results, has_blocking=True) is False


def test_post_pr_review_gh_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/7/merge")

    results = [(BUG_HUNTER, [SAMPLE_FINDING])]

    with patch(
        "gatehouse.github.subprocess.run", side_effect=FileNotFoundError
    ):
        assert post_pr_review(results, has_blocking=True) is False


def test_post_pr_review_skips_findings_without_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/7/merge")

    bad_finding = {**SAMPLE_FINDING, "file": "", "lineStart": 0}
    results = [(BUG_HUNTER, [bad_finding])]

    with patch("gatehouse.github.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        post_pr_review(results, has_blocking=True)

    payload = json.loads(mock_run.call_args.kwargs.get(
        "input", mock_run.call_args[1].get("input", "")
    ))
    assert "comments" not in payload
