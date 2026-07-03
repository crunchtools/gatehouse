"""Tests for review orchestration (mocked API)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from gatehouse.agents import BUG_HUNTER, CONSISTENCY_CHECK
from gatehouse.review import (
    BLOCKING_SEVERITIES,
    CONFIDENCE_THRESHOLD,
    _has_blocking_findings,
    load_constitution,
    run_review,
)

MOCK_BLOCKING_FINDINGS = json.dumps(
    [
        {
            "file": "src/app.py",
            "lineStart": 10,
            "lineEnd": 12,
            "severity": "high",
            "category": "bug",
            "description": "Null reference on user input",
            "suggestion": "Add null check",
            "evidence": "Line 10: user.name.lower()",
            "confidence": 95,
        },
    ]
)

MOCK_LOW_CONFIDENCE = json.dumps(
    [
        {
            "file": "src/app.py",
            "lineStart": 20,
            "lineEnd": 20,
            "severity": "high",
            "category": "bug",
            "description": "Possible issue",
            "suggestion": "Check it",
            "evidence": "Line 20: x = get_data()",
            "confidence": 60,
        },
    ]
)

MOCK_ADVISORY_FINDINGS = json.dumps(
    [
        {
            "file": "src/app.py",
            "lineStart": 1,
            "lineEnd": 1,
            "severity": "medium",
            "category": "quality",
            "description": "Inconsistent naming",
            "suggestion": "Use snake_case",
            "evidence": "Line 1: myVar = 1",
            "confidence": 90,
        },
    ]
)


def test_confidence_threshold_value() -> None:
    assert CONFIDENCE_THRESHOLD == 80


def test_blocking_severities() -> None:
    assert "critical" in BLOCKING_SEVERITIES
    assert "high" in BLOCKING_SEVERITIES
    assert "medium" not in BLOCKING_SEVERITIES
    assert "low" not in BLOCKING_SEVERITIES


@pytest.mark.asyncio
async def test_run_review_no_diff() -> None:
    """Empty diff exits 0."""
    with patch("gatehouse.review.get_git_diff", return_value=""):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=None,
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_blocking_finding() -> None:
    """High-severity finding from blocking agent exits 1."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_BLOCKING_FINDINGS,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 1


@pytest.mark.asyncio
async def test_run_review_advisory_mode() -> None:
    """Advisory mode exits 0 even with blocking findings."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_BLOCKING_FINDINGS,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=True,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_advisory_agent_only() -> None:
    """Advisory-only agents never cause exit 1."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_ADVISORY_FINDINGS,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["consistency"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_confidence_filtering() -> None:
    """Findings below confidence threshold are filtered out."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_LOW_CONFIDENCE,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_api_error_graceful() -> None:
    """API errors are caught gracefully, agent returns no findings."""
    import httpx

    mock_request = httpx.Request("POST", "https://example.com")
    mock_response = httpx.Response(429, request=mock_request)
    error = httpx.HTTPStatusError(
        "rate limited", request=mock_request, response=mock_response
    )
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            side_effect=error,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_invalid_json_graceful() -> None:
    """Invalid JSON from API is caught gracefully."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value="not valid json{{{",
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_empty_array_response() -> None:
    """Empty findings array exits 0."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value="[]",
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_multiple_agents() -> None:
    """Multiple agents run concurrently and results are aggregated."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_BLOCKING_FINDINGS,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs", "security", "performance"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 1


@pytest.mark.asyncio
async def test_gemini_retries_on_429() -> None:
    """call_gemini retries on 429 with backoff."""
    import httpx

    from gatehouse.gemini import call_gemini

    rate_limit_response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": "[]"}], "role": "model"}}
            ]
        },
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=[rate_limit_response, ok_response]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        text = await call_gemini(
            mock_client, "system", "user", "gemini-2.5-flash", "key"
        )

    assert text == "[]"
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_gemini_honors_retry_delay_in_body() -> None:
    """call_gemini sleeps for retryDelay from Gemini's structured error body."""
    import httpx

    from gatehouse.gemini import call_gemini

    error_body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "23s",
                },
            ],
        }
    }
    rate_limit_response = httpx.Response(
        429,
        json=error_body,
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": "[]"}], "role": "model"}}
            ]
        },
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=[rate_limit_response, ok_response]
    )

    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", sleep_mock):
        text = await call_gemini(
            mock_client, "system", "user", "gemini-2.5-flash", "key"
        )

    assert text == "[]"
    sleep_mock.assert_awaited_once_with(23.0)


@pytest.mark.asyncio
async def test_gemini_honors_retry_after_header() -> None:
    """call_gemini sleeps for Retry-After seconds when present."""
    import httpx

    from gatehouse.gemini import call_gemini

    rate_limit_response = httpx.Response(
        429,
        headers={"Retry-After": "7"},
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": "[]"}], "role": "model"}}
            ]
        },
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=[rate_limit_response, ok_response]
    )

    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", sleep_mock):
        text = await call_gemini(
            mock_client, "system", "user", "gemini-2.5-flash", "key"
        )

    assert text == "[]"
    sleep_mock.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_gemini_raises_after_max_retries() -> None:
    """call_gemini raises after exhausting retries."""
    import httpx

    from gatehouse.gemini import MAX_RETRIES, call_gemini

    rate_limit_response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=rate_limit_response)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await call_gemini(
            mock_client, "system", "user", "gemini-2.5-flash", "key"
        )

    assert mock_client.post.call_count == MAX_RETRIES


@pytest.mark.asyncio
async def test_run_review_stdin_diff() -> None:
    """stdin_diff bypasses git diff and uses the provided diff."""
    with (
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value="[]",
        ),
    ):
        exit_code = await run_review(
            stdin_diff="diff --git a/foo.py b/foo.py\n+print('hi')",
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_stdin_empty() -> None:
    """Empty stdin diff exits 0."""
    exit_code = await run_review(
        stdin_diff="",
        agent_slugs=None,
        model="gemini-2.5-flash",
        advisory=False,
        verbose=False,
        api_key="test-key",
    )
    assert exit_code == 0


def test_load_constitution_override(tmp_path: Path) -> None:
    """Explicit override path loads that file."""
    f = tmp_path / "custom.md"
    f.write_text("my constitution")
    assert load_constitution(str(f)) == "my constitution"


def test_load_constitution_override_missing(tmp_path: Path) -> None:
    """Explicit override path that doesn't exist exits 2."""
    with pytest.raises(SystemExit) as exc_info:
        load_constitution(str(tmp_path / "missing.md"))
    assert exc_info.value.code == 2


def test_load_constitution_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery finds files in priority order."""
    monkeypatch.chdir(tmp_path)
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("agents rules")
    assert load_constitution() == "agents rules"

    specify_dir = tmp_path / ".specify" / "memory"
    specify_dir.mkdir(parents=True)
    (specify_dir / "constitution.md").write_text("specify rules")
    assert load_constitution() == "specify rules"


def test_load_constitution_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns None when no constitution file found."""
    monkeypatch.chdir(tmp_path)
    assert load_constitution() is None


@pytest.mark.asyncio
async def test_run_review_constitution_skipped() -> None:
    """Constitution agent is skipped when no constitution file found."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.load_constitution", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value="[]",
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["constitution"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


def test_detect_default_branch_from_origin_head() -> None:
    """detect_default_branch reads origin/HEAD when available."""
    from gatehouse.review import detect_default_branch

    with patch(
        "gatehouse.review.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="refs/remotes/origin/master\n",
        ),
    ):
        assert detect_default_branch() == "master"


def test_detect_default_branch_fallback() -> None:
    """detect_default_branch falls back to checking main then master."""
    from gatehouse.review import detect_default_branch

    origin_fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
    main_fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
    master_ok = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="abc123\n"
    )

    with patch(
        "gatehouse.review.subprocess.run",
        side_effect=[origin_fail, main_fail, master_ok],
    ):
        assert detect_default_branch() == "master"


def test_has_blocking_findings_true() -> None:
    finding = {"severity": "high", "confidence": 95}
    results = [(BUG_HUNTER, [finding])]
    assert _has_blocking_findings(results) is True


def test_has_blocking_findings_false_advisory_agent() -> None:
    finding = {"severity": "high", "confidence": 95}
    results = [(CONSISTENCY_CHECK, [finding])]
    assert _has_blocking_findings(results) is False


def test_has_blocking_findings_false_low_severity() -> None:
    finding = {"severity": "low", "confidence": 95}
    results = [(BUG_HUNTER, [finding])]
    assert _has_blocking_findings(results) is False


def test_has_blocking_findings_empty() -> None:
    results = [(BUG_HUNTER, [])]
    assert _has_blocking_findings(results) is False


@pytest.mark.asyncio
async def test_run_review_comment_flag_calls_post() -> None:
    """When comment=True, post_pr_review is called."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_BLOCKING_FINDINGS,
        ),
        patch(
            "gatehouse.review.post_pr_review",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_post,
    ):
        await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
            comment=True,
        )
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_review_no_comment_flag_skips_post() -> None:
    """When comment=False (default), post_pr_review is not called."""
    with (
        patch("gatehouse.review.get_git_diff", return_value="some diff"),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_gemini",
            new_callable=AsyncMock,
            return_value=MOCK_BLOCKING_FINDINGS,
        ),
        patch(
            "gatehouse.review.post_pr_review",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="gemini-2.5-flash",
            advisory=False,
            verbose=False,
            api_key="test-key",
            comment=False,
        )
    mock_post.assert_not_awaited()


def test_load_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_env_file loads KEY=VALUE pairs without overwriting existing env."""
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("FOO=bar\nBAZ=qux\n# comment\n\nINVALID\n")

    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.setenv("BAZ", "existing")

    load_env_file(env_file)

    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "existing"

    monkeypatch.delenv("FOO", raising=False)


def test_load_env_file_missing(tmp_path: Path) -> None:
    """load_env_file does nothing when file doesn't exist."""
    from gatehouse.cli import load_env_file

    load_env_file(tmp_path / "nonexistent.env")


def test_load_env_file_rejects_spaces_in_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("FOO BAR=value\n")
    monkeypatch.delenv("FOO BAR", raising=False)
    load_env_file(env_file)
    assert "FOO BAR" not in os.environ


def test_load_env_file_rejects_dash_in_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("foo-bar=value\n")
    monkeypatch.delenv("foo-bar", raising=False)
    load_env_file(env_file)
    assert "foo-bar" not in os.environ


def test_load_env_file_accepts_lowercase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("http_proxy=http://proxy:3128\n")
    monkeypatch.delenv("http_proxy", raising=False)
    load_env_file(env_file)
    assert os.environ["http_proxy"] == "http://proxy:3128"
    monkeypatch.delenv("http_proxy", raising=False)


def test_load_env_file_rejects_leading_digit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("2FOO=value\n")
    monkeypatch.delenv("2FOO", raising=False)
    load_env_file(env_file)
    assert "2FOO" not in os.environ


@pytest.mark.asyncio
async def test_gemini_fallback_backoff_has_jitter() -> None:
    """Fallback backoff includes random jitter factor."""
    import httpx

    from gatehouse.gemini import INITIAL_BACKOFF, call_gemini

    rate_limit_response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": "[]"}], "role": "model"}}
            ]
        },
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=[rate_limit_response, ok_response]
    )

    sleep_mock = AsyncMock()
    with (
        patch("asyncio.sleep", sleep_mock),
        patch("gatehouse.gemini.random.random", return_value=0.25),
    ):
        await call_gemini(
            mock_client, "system", "user", "gemini-2.5-flash", "key"
        )

    expected = INITIAL_BACKOFF * (2 ** 0) * (0.5 + 0.25)
    sleep_mock.assert_awaited_once_with(expected)


def test_cli_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing GEMINI_API_KEY exits 2."""
    from gatehouse import cli

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "nonexistent.env")
    monkeypatch.setattr("sys.argv", ["gatehouse"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
