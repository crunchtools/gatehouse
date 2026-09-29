"""Tests for review orchestration (mocked API)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest

from gatehouse.agents import (
    BUG_HUNTER,
    CONSISTENCY_CHECK,
    DOCUMENTATION,
    SECURITY_SCAN,
    TEST_COVERAGE,
)
from gatehouse.llm import Completion
from gatehouse.review import (
    BLOCKING_SEVERITIES,
    CONFIDENCE_THRESHOLD,
    MAX_ADVISORY_LOWS,
    _cap_advisory_lows,
    _drop_reraised,
    _evidence_haystack,
    _evidence_is_real,
    _has_blocking_findings,
    load_constitution,
    run_review,
)

# Quotes every fixture's evidence: findings whose evidence is not in the
# diff are dropped before they are reported.
MOCK_DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
-my_var = 1
+myVar = 1
 x = get_data()
+user.name.lower()
"""


@pytest.fixture(autouse=True)
def github_threads():
    """CI sets GITHUB_REPOSITORY; never let a test fetch real review threads."""
    with patch("gatehouse.review.fetch_answered_threads", return_value=[]) as fetch:
        yield fetch


MOCK_BLOCKING_FINDINGS = json.dumps(
    [
        {
            "file": "src/app.py",
            "lineStart": 3,
            "lineEnd": 3,
            "severity": "high",
            "category": "bug",
            "description": "Null reference on user input",
            "suggestion": "Add null check",
            "evidence": "Line 3: user.name.lower()",
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
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_blocking_finding() -> None:
    """High-severity finding from blocking agent exits 1."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_BLOCKING_FINDINGS),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 1


@pytest.mark.asyncio
async def test_run_review_advisory_mode() -> None:
    """Advisory mode exits 0 even with blocking findings."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_BLOCKING_FINDINGS),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=True,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_advisory_agent_only() -> None:
    """Advisory-only agents never cause exit 1."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_ADVISORY_FINDINGS),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["consistency"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_confidence_filtering() -> None:
    """Findings below confidence threshold are filtered out."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_LOW_CONFIDENCE),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_api_error_graceful() -> None:
    """An agent whose API calls fail marks the review incomplete: exit 2."""
    import httpx

    mock_request = httpx.Request("POST", "https://example.com")
    mock_response = httpx.Response(429, request=mock_request)
    error = httpx.HTTPStatusError("rate limited", request=mock_request, response=mock_response)
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            side_effect=error,
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 2


@pytest.mark.asyncio
async def test_run_review_invalid_json_graceful() -> None:
    """Invalid JSON from the model marks the review incomplete: exit 2."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion("not valid json{{{"),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 2


@pytest.mark.asyncio
async def test_run_review_empty_array_response() -> None:
    """Empty findings array exits 0."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion("[]"),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_review_multiple_agents() -> None:
    """Multiple agents run concurrently and results are aggregated."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_BLOCKING_FINDINGS),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs", "security", "performance"],
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
        )
    assert exit_code == 1


@pytest.mark.asyncio
async def test_llm_retries_on_429() -> None:
    """call_model retries on 429 with backoff."""
    import httpx

    from gatehouse.llm import call_model

    rate_limit_response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": "[]"}}]},
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=[rate_limit_response, ok_response])

    with patch("asyncio.sleep", new_callable=AsyncMock):
        text = await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")

    assert text.text == "[]"
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_llm_honors_retry_after_header() -> None:
    """call_model sleeps for Retry-After seconds when present."""
    import httpx

    from gatehouse.llm import call_model

    rate_limit_response = httpx.Response(
        429,
        headers={"Retry-After": "7"},
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": "[]"}}]},
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=[rate_limit_response, ok_response])

    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", sleep_mock):
        text = await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")

    assert text.text == "[]"
    sleep_mock.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_llm_raises_after_max_retries() -> None:
    """call_model raises after exhausting retries."""
    import httpx

    from gatehouse.llm import MAX_RETRIES, call_model

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
        await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")

    assert mock_client.post.call_count == MAX_RETRIES


@pytest.mark.asyncio
async def test_run_review_stdin_diff() -> None:
    """stdin_diff bypasses git diff and uses the provided diff."""
    with (
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion("[]"),
        ),
    ):
        exit_code = await run_review(
            stdin_diff="diff --git a/foo.py b/foo.py\n+print('hi')",
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
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
        model="openai/gpt-6-luna",
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


def test_load_constitution_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery finds files in priority order."""
    monkeypatch.chdir(tmp_path)
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("agents rules")
    assert load_constitution() == "agents rules"

    specify_dir = tmp_path / ".specify" / "memory"
    specify_dir.mkdir(parents=True)
    (specify_dir / "constitution.md").write_text("specify rules")
    assert load_constitution() == "specify rules"


def test_load_constitution_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when no constitution file found."""
    monkeypatch.chdir(tmp_path)
    assert load_constitution() is None


@pytest.mark.asyncio
async def test_run_review_constitution_skipped() -> None:
    """Constitution agent is skipped when no constitution file found."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.load_constitution", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion("[]"),
        ),
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["constitution"],
            model="openai/gpt-6-luna",
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
    master_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123\n")

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
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_BLOCKING_FINDINGS),
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
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
            comment=True,
        )
    mock_post.assert_awaited_once()
    assert mock_post.call_args.args[1] is True


@pytest.mark.asyncio
async def test_run_review_advisory_posts_comment_not_request_changes() -> None:
    """Advisory mode posts a COMMENT review even when findings are blocking."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_BLOCKING_FINDINGS),
        ),
        patch(
            "gatehouse.review.post_pr_review",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_post,
    ):
        exit_code = await run_review(
            base="main",
            staged=False,
            agent_slugs=["bugs"],
            model="openai/gpt-6-luna",
            advisory=True,
            verbose=False,
            api_key="test-key",
            comment=True,
        )
    assert exit_code == 0
    mock_post.assert_awaited_once()
    assert mock_post.call_args.args[1] is False


@pytest.mark.asyncio
async def test_run_review_no_comment_flag_skips_post() -> None:
    """When comment=False (default), post_pr_review is not called."""
    with (
        patch("gatehouse.review.get_git_diff", return_value=MOCK_DIFF),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_BLOCKING_FINDINGS),
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
            model="openai/gpt-6-luna",
            advisory=False,
            verbose=False,
            api_key="test-key",
            comment=False,
        )
    mock_post.assert_not_awaited()


def test_load_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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

    before = dict(os.environ)
    load_env_file(tmp_path / "nonexistent.env")
    assert dict(os.environ) == before


def test_load_env_file_rejects_spaces_in_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("FOO BAR=value\n")
    monkeypatch.delenv("FOO BAR", raising=False)
    load_env_file(env_file)
    assert "FOO BAR" not in os.environ


def test_load_env_file_rejects_dash_in_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gatehouse.cli import load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text("foo-bar=value\n")
    monkeypatch.delenv("foo-bar", raising=False)
    load_env_file(env_file)
    assert "foo-bar" not in os.environ


def test_load_env_file_accepts_lowercase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
async def test_llm_fallback_backoff_has_jitter() -> None:
    """Fallback backoff includes random jitter factor."""
    import httpx

    from gatehouse.llm import INITIAL_BACKOFF, call_model

    rate_limit_response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.com"),
    )
    ok_response = httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": "[]"}}]},
        request=httpx.Request("POST", "https://example.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=[rate_limit_response, ok_response])

    sleep_mock = AsyncMock()
    with (
        patch("asyncio.sleep", sleep_mock),
        patch("gatehouse.llm.random.random", return_value=0.25),
    ):
        await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")

    expected = INITIAL_BACKOFF * (2**0) * (0.5 + 0.25)
    sleep_mock.assert_awaited_once_with(expected)


def test_cli_missing_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Missing OPENROUTER_API_KEY exits 2."""
    from gatehouse import cli

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY_FILE", raising=False)
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "nonexistent.env")
    monkeypatch.setattr("sys.argv", ["gatehouse"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2


def _ok(content: str) -> Any:
    import httpx

    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        request=httpx.Request("POST", "https://example.com"),
    )


@pytest.mark.asyncio
async def test_llm_payload_pins_zdr_and_fallback() -> None:
    """Every request enforces ZDR/no-training and lists the fallback model."""
    import httpx

    from gatehouse.llm import FALLBACK_MODELS, OPENROUTER_API_URL, call_model

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_ok("[]"))
    await call_model(mock_client, "sys", "user", "openai/gpt-6-luna", "k")

    args, kwargs = mock_client.post.call_args
    assert args[0] == OPENROUTER_API_URL
    body = kwargs["json"]
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}
    assert body["models"] == ["openai/gpt-6-luna", *FALLBACK_MODELS]
    assert kwargs["headers"]["Authorization"] == "Bearer k"


@pytest.mark.asyncio
async def test_llm_fallback_not_duplicated() -> None:
    """Requesting the fallback model itself does not list it twice."""
    import httpx

    from gatehouse.llm import FALLBACK_MODELS, call_model

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_ok("[]"))
    await call_model(mock_client, "sys", "user", FALLBACK_MODELS[0], "k")
    assert mock_client.post.call_args.kwargs["json"]["models"] == [FALLBACK_MODELS[0]]


@pytest.mark.asyncio
async def test_llm_retries_upstream_error_in_200() -> None:
    """A 200 carrying an upstream error object is retried, not parsed."""
    import httpx

    from gatehouse.llm import call_model

    error_200 = httpx.Response(
        200,
        json={"error": {"code": 429, "message": "rate-limited upstream"}},
        request=httpx.Request("POST", "https://example.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=[error_200, _ok("[]")])
    with patch("asyncio.sleep", new_callable=AsyncMock):
        text = await call_model(mock_client, "s", "u", "openai/gpt-6-luna", "k")
    assert text.text == "[]"
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_llm_strips_code_fences() -> None:
    """A fenced JSON reply is returned as bare JSON."""
    import httpx

    from gatehouse.llm import call_model

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_ok('```json\n[{"a": 1}]\n```'))
    text = await call_model(mock_client, "s", "u", "openai/gpt-6-luna", "k")
    assert text.text == '[{"a": 1}]'


@pytest.mark.asyncio
async def test_run_agent_accepts_wrapped_array() -> None:
    """{"findings": [...]} is unwrapped rather than dropped."""
    import httpx

    from gatehouse import review
    from gatehouse.agents import BUG_HUNTER

    finding = {
        "file": "a.py",
        "lineStart": 1,
        "lineEnd": 1,
        "severity": "high",
        "category": "bug",
        "description": "d",
        "suggestion": "s",
        "evidence": "e",
        "confidence": 90,
    }
    with patch(
        "gatehouse.review.call_model",
        new_callable=AsyncMock,
        return_value=Completion(json.dumps({"findings": [finding]})),
    ):
        _, findings, _ = await review.run_agent(
            AsyncMock(spec=httpx.AsyncClient),
            BUG_HUNTER,
            user_prompt="u",
            model="openai/gpt-6-luna",
            api_key="k",
            verbose=False,
            semaphore=asyncio.Semaphore(1),
        )
    assert findings == [finding]


def test_cli_key_file_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """OPENROUTER_API_KEY_FILE takes precedence and is stripped."""
    from gatehouse import cli

    key_file = tmp_path / "key"
    key_file.write_text("from-file\n")
    key_file.chmod(0o600)
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    monkeypatch.setenv("OPENROUTER_API_KEY_FILE", str(key_file))
    assert cli.load_api_key() == "from-file"


@pytest.mark.asyncio
async def test_run_review_agent_failure_advisory_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--advisory never fails the run; an unfinished agent is still reported."""
    import httpx

    with (
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timed out"),
        ),
    ):
        exit_code = await run_review(
            stdin_diff=MOCK_DIFF,
            agent_slugs=["bugs"],
            advisory=True,
            api_key="test-key",
        )
    assert exit_code == 0
    assert "Review incomplete: Bug Hunter" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_review_partial_failure_still_reports_findings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Findings from agents that finished are printed; failed ones are named."""
    import httpx

    async def fake(_c: Any, system_prompt: str, *_a: Any) -> Completion:
        if system_prompt == BUG_HUNTER.system_prompt:
            return Completion(MOCK_BLOCKING_FINDINGS)
        raise httpx.ReadTimeout("timed out")

    with (
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", side_effect=fake),
    ):
        exit_code = await run_review(
            stdin_diff=MOCK_DIFF,
            agent_slugs=["bugs", "consistency"],
            api_key="test-key",
        )
    out = capsys.readouterr()
    assert exit_code == 2
    assert "Null reference on user input" in out.out
    assert CONSISTENCY_CHECK.name in out.err


@pytest.mark.asyncio
async def test_llm_retries_on_read_timeout() -> None:
    """call_model retries a transport timeout and returns the next answer."""
    import httpx

    from gatehouse.llm import call_model

    ok = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "[]"}}]},
        request=httpx.Request("POST", "https://example.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=[httpx.ReadTimeout("slow"), ok])

    with patch("asyncio.sleep", new_callable=AsyncMock):
        text = await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")
    assert text.text == "[]"
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_llm_raises_timeout_after_max_retries() -> None:
    """A timeout on every attempt surfaces as the timeout, not a crash elsewhere."""
    import httpx

    from gatehouse.llm import MAX_RETRIES, call_model

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))

    sleep_mock = AsyncMock()
    with (
        patch("asyncio.sleep", sleep_mock),
        pytest.raises(httpx.ReadTimeout),
    ):
        await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")
    assert mock_client.post.call_count == MAX_RETRIES
    assert sleep_mock.await_count == MAX_RETRIES - 1


@pytest.mark.asyncio
async def test_run_review_non_numeric_confidence_is_agent_failure() -> None:
    """A malformed finding marks the agent unfinished instead of crashing."""
    bad = json.dumps([{"file": "a.py", "severity": "high", "confidence": "high"}])
    with (
        patch("gatehouse.review.get_file_listing", return_value="a.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, return_value=Completion(bad)),
    ):
        exit_code = await run_review(stdin_diff=MOCK_DIFF, agent_slugs=["bugs"], api_key="test-key")
    assert exit_code == 2


@pytest.mark.asyncio
async def test_run_review_comment_names_failed_agents() -> None:
    """The posted review is told which agents did not finish."""
    import httpx

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("slow"),
        ),
        patch(
            "gatehouse.review.post_pr_review", new_callable=AsyncMock, return_value=True
        ) as mock_post,
    ):
        await run_review(stdin_diff=MOCK_DIFF, agent_slugs=["bugs"], api_key="k", comment=True)
    assert mock_post.call_args.args[2] == (BUG_HUNTER.name,)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ['{"verdict": "fine"}', '"ok"', '["not a finding"]'])
async def test_run_review_wrong_shape_is_agent_failure(reply: str) -> None:
    """A reply that is not a list of finding objects does not pass as clean."""
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model", new_callable=AsyncMock, return_value=Completion(reply)
        ),
    ):
        exit_code = await run_review(stdin_diff=MOCK_DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": []},
        {"choices": {"a": 1}},
        {"choices": ["x"]},
        {"choices": [{"message": 1}]},
        {"choices": [{"message": {"content": "  "}}]},
    ],
)
async def test_llm_malformed_body_is_retried(body: Any) -> None:
    """A 200 body of the wrong shape is retried like an upstream error."""
    import httpx

    from gatehouse.llm import MAX_RETRIES, call_model

    response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.com"))
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=response)
    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await call_model(mock_client, "system", "user", "openai/gpt-6-luna", "key")
    assert mock_client.post.call_count == MAX_RETRIES


@pytest.mark.asyncio
async def test_run_review_advisory_blocking_and_failed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Advisory with a blocking finding and a failed agent: both shown, exit 0."""
    import httpx

    async def fake(_c: Any, system_prompt: str, *_a: Any) -> Completion:
        if system_prompt == BUG_HUNTER.system_prompt:
            return Completion(MOCK_BLOCKING_FINDINGS)
        raise httpx.ReadTimeout("timed out")

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", side_effect=fake),
    ):
        exit_code = await run_review(
            stdin_diff=MOCK_DIFF,
            agent_slugs=["bugs", "consistency"],
            advisory=True,
            api_key="k",
        )
    out = capsys.readouterr()
    assert exit_code == 0
    assert "Null reference on user input" in out.out
    assert "Exit: 0" in out.out
    assert f"Review incomplete: {CONSISTENCY_CHECK.name}" in out.err


@pytest.mark.asyncio
async def test_run_review_incomplete_summary_says_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The printed summary agrees with the returned exit code."""
    import httpx

    async def fake(_c: Any, system_prompt: str, *_a: Any) -> Completion:
        if system_prompt == BUG_HUNTER.system_prompt:
            return Completion(MOCK_BLOCKING_FINDINGS)
        raise httpx.ReadTimeout("timed out")

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", side_effect=fake),
    ):
        exit_code = await run_review(
            stdin_diff=MOCK_DIFF, agent_slugs=["bugs", "consistency"], api_key="k"
        )
    assert exit_code == 2
    assert "Exit: 2 (review incomplete)" in capsys.readouterr().out


# --- #56: evidence check, re-raise suppression, advisory LOW cap ---

CASES_DIFF = """\
diff --git a/src/cases.py b/src/cases.py
--- a/src/cases.py
+++ b/src/cases.py
@@ -10,2 +10,4 @@
-def _cases(split, csv_bytes):
+def _cases(split: str, csv_bytes: bytes) -> list[Case]:
+    rows = [Case.from_row(r) for r in csv.reader(io.StringIO(csv_bytes.decode()))]
     return rows
"""


@pytest.mark.parametrize(
    "evidence",
    [
        "def _cases(split: str, csv_bytes: bytes) -> list[Case]:",
        "Line 11: def _cases(split: str, csv_bytes: bytes) -> list[Case]:",
        "   11 [+]| def _cases(split: str, csv_bytes: bytes) -> list[Case]:",
        "-def _cases(split, csv_bytes):",
        (
            "```python\nrows = [Case.from_row(r)\n"
            "    for r in csv.reader(io.StringIO(csv_bytes.decode()))]\n```"
        ),
        "def _cases(split: str, ...) -> list[Case]:",
        "",
        "n/a",
    ],
)
def test_real_evidence_is_kept(evidence: str) -> None:
    assert _evidence_is_real(evidence, _evidence_haystack(CASES_DIFF))


@pytest.mark.parametrize(
    "evidence",
    [
        # mcp-trentina#258: the quoted signature does not exist in the code.
        "def _cases(split: str, csv_bytes: bytes):",
        "def _cases(split: str, csv_bytes: bytes) -> list[Case]:\n    cache.clear()",
    ],
)
def test_fabricated_evidence_is_dropped(evidence: str) -> None:
    assert not _evidence_is_real(evidence, _evidence_haystack(CASES_DIFF))


def test_evidence_may_quote_the_constitution() -> None:
    haystack = _evidence_haystack(CASES_DIFF, None, "Every repo MUST keep a CHANGELOG.md.")
    assert _evidence_is_real("Every repo MUST keep a CHANGELOG.md", haystack)


@pytest.mark.asyncio
async def test_run_review_drops_fabricated_high_finding() -> None:
    fabricated = json.dumps(
        [{**json.loads(MOCK_BLOCKING_FINDINGS)[0], "evidence": "user.email.lower()"}]
    )
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(fabricated),
        ),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as mock_post,
    ):
        exit_code = await run_review(
            stdin_diff=MOCK_DIFF, agent_slugs=["bugs"], api_key="k", comment=True
        )
    assert exit_code == 0
    assert mock_post.call_args.args[0] == [(BUG_HUNTER, [])]
    assert mock_post.call_args.kwargs["unverified"] == 1


def _finding(line: int, severity: str = "low", path: str = "src/app.py", confidence: int = 90):
    return {"file": path, "lineStart": line, "severity": severity, "confidence": confidence}


def test_reraise_near_answered_thread_is_dropped() -> None:
    threads = [("Consistency Check", "src/app.py", 40)]
    results = [
        (
            CONSISTENCY_CHECK,
            [_finding(44), _finding(35), _finding(46), _finding(40, path="src/other.py")],
        ),
        (DOCUMENTATION, [_finding(40)]),
    ]
    kept, dropped = _drop_reraised(results, threads)
    assert dropped == 2
    assert kept == [
        (CONSISTENCY_CHECK, [_finding(46), _finding(40, path="src/other.py")]),
        (DOCUMENTATION, [_finding(40)]),
    ]


def test_medium_reraise_is_dropped() -> None:
    results = [(CONSISTENCY_CHECK, [_finding(42, "medium")])]
    kept, dropped = _drop_reraised(results, [("Consistency Check", "src/app.py", 40)])
    assert (kept, dropped) == ([(CONSISTENCY_CHECK, [])], 1)


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_serious_finding_near_answered_thread_is_kept(severity: str) -> None:
    results = [(BUG_HUNTER, [_finding(40, severity)])]
    kept, dropped = _drop_reraised(results, [("Bug Hunter", "src/app.py", 40)])
    assert (kept, dropped) == (results, 0)


@pytest.mark.asyncio
async def test_run_review_suppresses_reraise_only_when_commenting(
    github_threads: AsyncMock,
) -> None:
    github_threads.return_value = [("Consistency Check", "src/app.py", 1)]
    common = {
        "stdin_diff": MOCK_DIFF,
        "agent_slugs": ["consistency"],
        "api_key": "k",
    }
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion(MOCK_ADVISORY_FINDINGS),
        ),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as mock_post,
        patch("gatehouse.review.format_results") as mock_format,
    ):
        await run_review(**common, comment=False)
        assert mock_format.call_args.args[0] == [
            (CONSISTENCY_CHECK, json.loads(MOCK_ADVISORY_FINDINGS))
        ]
        await run_review(**common, comment=True)
    assert mock_post.call_args.args[0] == [(CONSISTENCY_CHECK, [])]
    assert mock_post.call_args.kwargs["reraised"] == 1


def test_cap_keeps_most_confident_advisory_lows() -> None:
    docs = [_finding(i, confidence=80 + i) for i in range(4)]
    tests = [_finding(i, confidence=90 + i) for i in range(4)]
    bugs = [_finding(i, confidence=80) for i in range(3)]
    security = [_finding(1, confidence=80)]
    medium = [_finding(1, "medium", confidence=80)]
    results = [
        (DOCUMENTATION, docs + medium),
        (TEST_COVERAGE, tests),
        (BUG_HUNTER, bugs),
        (SECURITY_SCAN, security),
    ]
    kept, capped = _cap_advisory_lows(results)
    assert capped == 8 - MAX_ADVISORY_LOWS
    assert kept == [
        (DOCUMENTATION, [docs[3], *medium]),
        (TEST_COVERAGE, tests),
        (BUG_HUNTER, bugs),
        (SECURITY_SCAN, security),
    ]


def test_cap_leaves_small_reviews_alone() -> None:
    results = [(DOCUMENTATION, [_finding(1)]), (BUG_HUNTER, [_finding(2)])]
    assert _cap_advisory_lows(results) == (results, 0)


def test_cap_exempts_every_required_low_agent() -> None:
    docs = [_finding(i, confidence=90) for i in range(MAX_ADVISORY_LOWS + 2)]
    results = [(DOCUMENTATION, docs), (BUG_HUNTER, [_finding(1)])]
    assert _cap_advisory_lows(results, frozenset({"Documentation"})) == (results, 0)
    kept, capped = _cap_advisory_lows(results, frozenset())
    assert capped == 3
    assert sum(len(f) for _, f in kept) == MAX_ADVISORY_LOWS


def _ok_body(**extra: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "[]"}}], **extra},
        request=httpx.Request("POST", "https://example.com"),
    )


@pytest.mark.asyncio
async def test_llm_returns_serving_model_and_usage() -> None:
    from gatehouse.llm import call_model

    usage = {
        "prompt_tokens": 15690,
        "completion_tokens": 327,
        "completion_tokens_details": {"reasoning_tokens": 320},
        "cost": 0.0021,
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        return_value=_ok_body(model="google/gemini-3.1-flash-lite", usage=usage)
    )
    reply = await call_model(mock_client, "s", "u", "openai/gpt-6-luna", "k")
    assert reply.model == "google/gemini-3.1-flash-lite"
    assert reply.fallback is True
    assert reply.tokens() == (15690, 327, 320, 0.0021)


@pytest.mark.asyncio
async def test_llm_reply_without_usage_is_zero() -> None:
    from gatehouse.llm import call_model

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_ok_body(usage="junk"))
    reply = await call_model(mock_client, "s", "u", "openai/gpt-6-luna", "k")
    assert reply.fallback is False
    assert reply.tokens() == (0, 0, 0, 0.0)


def test_dated_variant_of_requested_model_is_not_fallback() -> None:
    reply = Completion("[]", requested="openai/gpt-6-luna", model="openai/gpt-6-luna-20260901")
    assert reply.fallback is False


def _served(model: str, requested: str = "openai/gpt-6-luna") -> Completion:
    return Completion(
        "[]",
        requested=requested,
        model=model,
        usage={"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.001},
    )


@pytest.mark.asyncio
async def test_run_review_logs_usage_and_reports_fallback(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = tmp_path / "summary.md"
    usage_json = tmp_path / "usage.json"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    async def fake(_c: Any, system_prompt: str, *_a: Any) -> Completion:
        if system_prompt == BUG_HUNTER.system_prompt:
            return _served("openai/gpt-6-luna")
        return _served("google/gemini-3.1-flash-lite")

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", side_effect=fake),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
    ):
        await run_review(
            stdin_diff=MOCK_DIFF,
            agent_slugs=["bugs", "security"],
            api_key="k",
            comment=True,
            usage_json=str(usage_json),
        )
    err = capsys.readouterr().err
    assert (
        "agent=bugs model=openai/gpt-6-luna prompt=100 completion=10 reasoning=0 "
        "cost=$0.0010 fallback=no"
    ) in err
    assert "agent=security model=google/gemini-3.1-flash-lite" in err
    assert "fallback=yes" in err
    assert "usage: calls=2 prompt=200 completion=20 reasoning=0 cost=$0.0020 fallback=1/2" in err
    assert "| **total** |" in summary.read_text()
    assert json.loads(usage_json.read_text())["total"]["fallback"] == 1
    kwargs = post.call_args.kwargs
    assert kwargs["fallbacks"] == {"google/gemini-3.1-flash-lite": 1}
    assert kwargs["agent_count"] == 2
    assert kwargs["scope"] == "Full review."


# The PR diff: app.py lines 1-3 plus util.py line 1.
PR_DIFF = (
    MOCK_DIFF
    + """\
diff --git a/src/util.py b/src/util.py
--- a/src/util.py
+++ b/src/util.py
@@ -0,0 +1 @@
+def helper(): pass
"""
)

# Since the last review: only util.py changed, plus a base-branch file
# merged in that the PR itself does not touch.
SINCE_DIFF = """\
diff --git a/src/util.py b/src/util.py
--- a/src/util.py
+++ b/src/util.py
@@ -0,0 +1 @@
+def helper(): pass
diff --git a/src/from_base.py b/src/from_base.py
--- a/src/from_base.py
+++ b/src/from_base.py
@@ -0,0 +1 @@
+merged_from_base = True
"""


@pytest.fixture
def pr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "crunchtools/gatehouse")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/7/merge")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GATEHOUSE_HEAD_SHA", "head1234567")
    monkeypatch.delenv("GATEHOUSE_CONTEXT_REPO", raising=False)
    monkeypatch.delenv("GATEHOUSE_CONTEXT_REF", raising=False)


async def _incremental_run(
    last: str | None, compared: tuple[str, int] | None, reply: str = "[]"
) -> tuple[str, AsyncMock]:
    """Run an incremental review; return the prompt the agent saw and the post mock."""
    call_model = AsyncMock(return_value=Completion(reply))
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.fetch_last_reviewed_commit", return_value=last),
        patch("gatehouse.review.fetch_compare", return_value=compared) as compare,
        patch("gatehouse.review.call_model", call_model),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
    ):
        await run_review(
            stdin_diff=PR_DIFF,
            agent_slugs=["bugs"],
            api_key="k",
            comment=True,
            incremental=True,
        )
    if last is not None:
        compare.assert_called_once_with(last, "head1234567")
    return call_model.call_args.args[2], post


@pytest.mark.asyncio
@pytest.mark.usefixtures("pr_env")
async def test_incremental_reviews_only_the_compare_range() -> None:
    prompt, post = await _incremental_run("last7654321", (SINCE_DIFF, 2))
    assert "def helper()" in prompt
    assert "user.name.lower()" not in prompt  # reviewed already
    assert "merged_from_base" not in prompt  # not the PR's change
    kwargs = post.call_args.kwargs
    assert kwargs["scope"] == "Reviewed 2 commits since last765 (incremental)."
    assert kwargs["commit_id"] == "head1234567"


@pytest.mark.asyncio
@pytest.mark.usefixtures("pr_env")
async def test_incremental_force_push_falls_back_to_full() -> None:
    prompt, post = await _incremental_run("last7654321", None)
    assert "user.name.lower()" in prompt
    assert post.call_args.kwargs["scope"] == "Full review."


@pytest.mark.asyncio
@pytest.mark.usefixtures("pr_env")
async def test_incremental_without_earlier_review_falls_back_to_full() -> None:
    prompt, post = await _incremental_run(None, None)
    assert "user.name.lower()" in prompt
    assert post.call_args.kwargs["scope"] == "Full review."


@pytest.mark.asyncio
@pytest.mark.usefixtures("pr_env")
async def test_finding_outside_pr_diff_is_not_posted() -> None:
    inside = {**json.loads(MOCK_BLOCKING_FINDINGS)[0], "evidence": "user.name.lower()"}
    outside = {**inside, "lineStart": 40, "lineEnd": 40}
    _, post = await _incremental_run(None, None, json.dumps([inside, outside]))
    posted = post.call_args.args[0][0][1]
    assert [f["lineStart"] for f in posted] == [3]
    assert post.call_args.kwargs["offdiff"] == 1


def test_right_side_lines_counts_added_and_context_only() -> None:
    from gatehouse.diffview import right_side_lines

    assert right_side_lines(PR_DIFF) == {"src/app.py": {1, 2, 3}, "src/util.py": {1}}
    assert right_side_lines("not a diff") is None


def test_restrict_diff_keeps_only_listed_files() -> None:
    from gatehouse.ignore import diff_paths, restrict_diff

    assert diff_paths(PR_DIFF) == {"src/app.py", "src/util.py"}
    kept = restrict_diff(SINCE_DIFF, {"src/util.py"})
    assert "helper" in kept
    assert "from_base" not in kept
    assert restrict_diff(SINCE_DIFF, {"nope.py"}) == ""
