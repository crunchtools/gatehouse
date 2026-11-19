"""Tests for oversize diff handling: deletions elided, the rest refused before any call."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gatehouse.diffview import OMITTED_PREFIX, render_diff_view
from gatehouse.llm import Completion
from gatehouse.oversize import (
    MAX_DIFF_BYTES_ENV,
    elide_deletions,
    oversize_message,
)
from gatehouse.review import run_review

MODIFIED = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,2 @@
 x = 1
+y = 2
"""


def _deleted(path: str, lines: int) -> str:
    body = "".join(f"-dump row {i}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        "index 1234567..0000000\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        f"@@ -1,{lines} +0,0 @@\n"
        f"{body}"
    )


def test_large_deletion_is_elided() -> None:
    diff, elided = elide_deletions(_deleted("backup/dump.sql", 5000) + MODIFIED, limit=1024)
    assert elided == ["backup/dump.sql"]
    assert "dump row" not in diff
    assert f"{OMITTED_PREFIX}5000 deleted line(s)" in diff
    assert diff.endswith(MODIFIED)


def test_small_deletion_is_kept() -> None:
    """A removed protection stays visible: small deletions are sent whole."""
    original = _deleted("src/auth.py", 3) + MODIFIED
    assert elide_deletions(original, limit=1024) == (original, [])


def test_large_modification_is_not_elided() -> None:
    added = MODIFIED + "".join(f"+line {i}\n" for i in range(5000))
    assert elide_deletions(added, limit=1024) == (added, [])


def test_elision_is_idempotent() -> None:
    once, _ = elide_deletions(_deleted("dump.sql", 5000), limit=1024)
    assert elide_deletions(once, limit=1024) == (once, [])


def test_elided_deletion_renders_as_deleted_with_a_note() -> None:
    diff, _ = elide_deletions(_deleted("dump.sql", 5000), limit=1024)
    view = render_diff_view(diff)
    assert view is not None
    assert "### dump.sql (deleted)" in view
    assert "5000 deleted line(s)" in view
    assert "too large to send for review" in view


def test_oversize_message_names_limit_and_largest_file() -> None:
    big = MODIFIED + "".join(f"+line {i}\n" for i in range(500))
    message = oversize_message(big, limit=100)
    assert message is not None
    assert "100-byte limit" in message
    assert MAX_DIFF_BYTES_ENV in message
    assert "src/app.py" in message
    assert oversize_message(MODIFIED, limit=len(MODIFIED)) is None


@pytest.mark.asyncio
async def test_run_review_refuses_oversize_diff_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#160: an oversize diff exits 2 with a reason, and no model is called."""
    monkeypatch.setenv(MAX_DIFF_BYTES_ENV, "200")
    big = MODIFIED + "".join(f"+line {i}\n" for i in range(500))
    with patch("gatehouse.review.call_model", new_callable=AsyncMock) as call:
        exit_code = await run_review(stdin_diff=big, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 2
    call.assert_not_called()
    assert "200-byte limit" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_review_sends_large_deletion_elided(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A deletion over the per-file threshold is reviewed as a header and a note."""
    diff = _deleted("backup/dump.sql", 20000) + MODIFIED
    with (
        patch("gatehouse.review.get_file_listing", return_value="src/app.py"),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=Completion("[]"),
        ) as call,
    ):
        exit_code = await run_review(stdin_diff=diff, agent_slugs=["bugs"], samples=1, api_key="k")
    assert exit_code == 0
    prompt = call.call_args.args[2]
    assert "dump row" not in prompt
    assert "backup/dump.sql (deleted)" in prompt
    assert "Elided 1 large deleted file(s): backup/dump.sql." in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_review_rejects_bad_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_DIFF_BYTES_ENV, "lots")
    with patch("gatehouse.review.call_model", new_callable=AsyncMock) as call:
        assert await run_review(stdin_diff=MODIFIED, api_key="k") == 2
    call.assert_not_called()
