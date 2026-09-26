""".gatehouse-ignore: which file sections leave the diff, and where the rules come from."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from gatehouse.ignore import IGNORE_FILE, filter_diff, filter_listing, parse_ignore
from gatehouse.review import run_review

if TYPE_CHECKING:
    from pathlib import Path

MODIFIED = """diff --git a/{p} b/{p}
index 1111111..2222222 100644
--- a/{p}
+++ b/{p}
@@ -1,2 +1,2 @@
 keep
-old
+new
"""

DELETED = """diff --git a/{p} b/{p}
deleted file mode 100644
index 1111111..0000000
--- a/{p}
+++ /dev/null
@@ -1,2 +0,0 @@
-line one
--- a/src/app.py
"""

ADDED = """diff --git a/{p} b/{p}
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/{p}
@@ -0,0 +1 @@
+hello
"""

BINARY = """diff --git a/{p} b/{p}
index 1111111..2222222 100644
Binary files a/{p} and b/{p} differ
"""

RENAME = """diff --git a/{old} b/{new}
similarity index 100%
rename from {old}
rename to {new}
"""


def _spec(*lines: str) -> Any:
    return parse_ignore("\n".join(lines))


def test_no_ignore_file_changes_nothing() -> None:
    diff = MODIFIED.format(p="src/app.py")
    assert parse_ignore(None) is None
    assert parse_ignore("  \n") is None
    assert filter_diff(diff, None) == (diff, [])


def test_ignored_sections_drop_and_others_stay() -> None:
    diff = MODIFIED.format(p="src/app.py") + DELETED.format(p="data/x.fp")
    kept, ignored = filter_diff(diff, _spec("*.fp"))
    assert kept == MODIFIED.format(p="src/app.py")
    assert ignored == ["data/x.fp"]


def test_hunk_line_that_looks_like_a_header_is_not_a_path() -> None:
    """The deleted data file contains "-- a/src/app.py"; it is still ignored."""
    _, ignored = filter_diff(DELETED.format(p="data/x.fp"), _spec("data/"))
    assert ignored == ["data/x.fp"]


def test_added_and_binary_files_match_by_path() -> None:
    diff = ADDED.format(p="fixtures/a.json") + BINARY.format(p="fixtures/b.png")
    kept, ignored = filter_diff(diff + MODIFIED.format(p="x.py"), _spec("fixtures/"))
    assert ignored == ["fixtures/a.json", "fixtures/b.png"]
    assert kept == MODIFIED.format(p="x.py")


def test_rename_out_of_an_ignored_dir_is_reviewed() -> None:
    diff = RENAME.format(old="vendor/lib.py", new="src/lib.py")
    assert filter_diff(diff, _spec("vendor/")) == (diff, [])
    both_ignored = RENAME.format(old="vendor/a.py", new="vendor/b.py")
    assert filter_diff(both_ignored, _spec("vendor/")) == ("", ["vendor/a.py -> vendor/b.py"])


def test_negation_follows_gitignore() -> None:
    diff = MODIFIED.format(p="data/keep.fp") + MODIFIED.format(p="data/drop.fp")
    kept, ignored = filter_diff(diff, _spec("data/*", "!data/keep.fp"))
    assert ignored == ["data/drop.fp"]
    assert "data/keep.fp" in kept


def test_the_ignore_file_itself_is_always_reviewed() -> None:
    diff = MODIFIED.format(p=IGNORE_FILE)
    assert filter_diff(diff, _spec("*")) == (diff, [])


def test_listing_drops_ignored_paths() -> None:
    listing = "src/app.py\ndata/a.fp\ndata/b.fp\n"
    assert filter_listing(listing, _spec("*.fp")) == "src/app.py\n"
    assert filter_listing(None, _spec("*.fp")) is None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def patch_series(tmp_path: Path) -> str:
    """Two commits as `gh pr diff --patch` delivers them: mail headers and all."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.test")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "base").write_text("x\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "big.fp").write_text("fingerprint\n" * 50)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "add data")
    (tmp_path / "app.py").write_text("print('hi')\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "add code")
    return _git(tmp_path, "format-patch", "--stdout", "main~2")


def test_patch_series_keeps_code_and_headers(patch_series: str) -> None:
    kept, ignored = filter_diff(patch_series, _spec("data/"))
    assert ignored == ["data/big.fp"]
    assert "fingerprint" not in kept
    assert "+print('hi')" in kept
    assert "Subject: [PATCH 2/2] add code" in kept


def test_patch_series_all_ignored_is_empty(patch_series: str) -> None:
    kept, ignored = filter_diff(patch_series, _spec("data/", "*.py"))
    assert kept == ""
    assert ignored == ["app.py", "data/big.fp"]


@pytest.mark.asyncio
async def test_run_review_uses_the_base_ignore_file_not_the_prs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under a trusted-base context, a PR's own ignore file has no say."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / IGNORE_FILE).write_text("*\n")  # what a hostile PR would ship
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REPO", "o/r")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REF", "basesha")

    def fetch(_repo: str, _ref: str, path: str, _token: str) -> str | None:
        return "data/\n" if path == IGNORE_FILE else None

    diff = MODIFIED.format(p="src/app.py") + MODIFIED.format(p="data/x.fp")
    call_model = AsyncMock(return_value="[]")
    with (
        patch("gatehouse.review.fetch_repo_file", side_effect=fetch),
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.call_model", call_model),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
    ):
        exit_code = await run_review(
            stdin_diff=diff, agent_slugs=["bugs"], api_key="k", comment=True
        )
    assert exit_code == 0
    prompt = call_model.call_args.args[2]
    assert "src/app.py" in prompt
    assert "data/x.fp" not in prompt
    assert post.call_args.args[3] == 1


@pytest.mark.asyncio
async def test_run_review_all_ignored_skips_the_agents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / IGNORE_FILE).write_text("data/\n")
    call_model = AsyncMock(return_value="[]")
    with (
        patch("gatehouse.review.call_model", call_model),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
    ):
        exit_code = await run_review(
            stdin_diff=MODIFIED.format(p="data/x.fp"),
            agent_slugs=["bugs"],
            api_key="k",
            comment=True,
        )
    out = capsys.readouterr()
    assert exit_code == 0
    call_model.assert_not_awaited()
    post.assert_not_awaited()
    assert "No changes to review." in out.out
    assert f"Ignored 1 file(s) per {IGNORE_FILE}." in out.err


def test_copy_from_path_is_read_whole() -> None:
    copy = (
        "diff --git a/data/a.fp b/data/b.fp\n"
        "similarity index 100%\ncopy from data/a.fp\ncopy to data/b.fp\n"
    )
    assert filter_diff(copy, _spec("/data/a.fp", "/data/b.fp"))[1] == ["data/a.fp -> data/b.fp"]


def test_quoted_and_spaced_paths_match(tmp_path: Path) -> None:
    """Git quotes non-ASCII paths and ends a path containing a space with a tab."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "caf\u00e9.fp").write_text("x\n")
    (tmp_path / "with space.fp").write_text("x\n")
    (tmp_path / "bin.fp").write_bytes(b"\x00\x01")
    _git(tmp_path, "add", ".")
    diff = _git(tmp_path, "-c", "core.quotePath=true", "diff", "--cached")
    kept, ignored = filter_diff(diff, _spec("*.fp"))
    assert kept == ""
    assert ignored == ["bin.fp", "caf\u00e9.fp", "with space.fp"]


def test_no_local_fallback_under_a_trusted_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Base has no ignore file; the PR's copy on disk must not be used."""
    from gatehouse.review import _load_context_file

    monkeypatch.chdir(tmp_path)
    (tmp_path / IGNORE_FILE).write_text("*\n")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REPO", "o/r")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REF", "basesha")
    with patch("gatehouse.review.fetch_repo_file", return_value=None):
        assert _load_context_file(IGNORE_FILE) is None


@pytest.mark.asyncio
async def test_run_review_filters_the_file_listing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / IGNORE_FILE).write_text("data/\n")
    call_model = AsyncMock(return_value="[]")
    with (
        patch("gatehouse.review.get_file_listing", return_value="src/app.py\ndata/zz.fp\n"),
        patch("gatehouse.review.call_model", call_model),
    ):
        await run_review(
            stdin_diff=MODIFIED.format(p="src/app.py"), agent_slugs=["bugs"], api_key="k"
        )
    prompt = call_model.call_args.args[2]
    assert "src/app.py" in prompt
    assert "data/zz.fp" not in prompt


def test_constitution_has_no_local_fallback_under_a_trusted_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gatehouse.review import load_constitution

    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("PR-supplied rules\n")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REPO", "o/r")
    monkeypatch.setenv("GATEHOUSE_CONTEXT_REF", "basesha")
    with patch("gatehouse.review.fetch_repo_file", return_value=None):
        assert load_constitution() is None
