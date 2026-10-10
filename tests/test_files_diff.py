"""The diff review.yml rebuilds from the PR's file list when the diff endpoint refuses.

GitHub will not serve the diff of a PR of more than 300 files (#75). The
workflow then pages through 'List pull request files' and stitches each
file's patch back into a unified diff. That jq program is run here, extracted
from the workflow between its marker comments, and its output fed to the
parsers gatehouse reads a diff with.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gatehouse.diffview import right_side_lines
from gatehouse.ignore import diff_paths, filter_diff, parse_ignore

REVIEW = Path(__file__).parent.parent / ".github" / "workflows" / "review.yml"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")

MODIFIED = {
    "filename": "src/app.py",
    "status": "modified",
    "patch": "@@ -1,2 +1,3 @@\n import os\n+import sys\n x = 1",
}
ADDED = {"filename": "docs/new.md", "status": "added", "patch": "@@ -0,0 +1,2 @@\n+# New\n+text"}
REMOVED = {"filename": "old.txt", "status": "removed", "patch": "@@ -1 +0,0 @@\n-gone"}
RENAMED = {
    "filename": "src/b.py",
    "previous_filename": "src/a.py",
    "status": "renamed",
    "patch": "@@ -5,1 +5,1 @@\n-old\n+new",
}
MOVED = {"filename": "img/b.png", "previous_filename": "img/a.png", "status": "renamed"}
BINARY = {"filename": "logo.png", "status": "modified"}


def _diff(*pages: list[dict]) -> str:
    block = re.search(
        r"# --- files to diff.*?\n(.*?)# --- end files to diff ---", REVIEW.read_text(), re.S
    )
    assert block, "files-to-diff markers missing from review.yml"
    program = re.search(r"jq -rs '(.*?)'", block.group(1), re.S)
    assert program, "jq program not found between the markers"
    # gh api --paginate emits one JSON array per page; -s slurps them.
    out = subprocess.run(
        ["jq", "-rs", program.group(1)],
        input="".join(json.dumps(page) for page in pages),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def test_rebuilt_diff_anchors_comments_on_real_lines() -> None:
    lines = right_side_lines(_diff([MODIFIED, ADDED], [REMOVED, RENAMED]))
    assert lines is not None
    assert lines["src/app.py"] == {1, 2, 3}
    assert lines["docs/new.md"] == {1, 2}
    assert lines["src/b.py"] == {5}
    assert lines["old.txt"] == set()


def test_rebuilt_diff_names_both_sides_of_a_rename() -> None:
    assert diff_paths(_diff([RENAMED, MOVED])) == {"src/a.py", "src/b.py", "img/a.png", "img/b.png"}


def test_rebuilt_diff_keeps_files_without_a_patch_as_headers() -> None:
    diff = _diff([BINARY, MODIFIED])
    assert "diff --git a/logo.png b/logo.png\n" in diff
    assert diff_paths(diff) == {"logo.png", "src/app.py"}
    assert right_side_lines(diff) == {"src/app.py": {1, 2, 3}}


def test_rebuilt_diff_honours_gatehouse_ignore() -> None:
    kept, ignored = filter_diff(_diff([MODIFIED, ADDED, BINARY]), parse_ignore("docs/\n*.png\n"))
    assert ignored == ["docs/new.md", "logo.png"]
    assert diff_paths(kept) == {"src/app.py"}


def test_no_files_is_an_empty_diff() -> None:
    assert _diff([]) == ""
