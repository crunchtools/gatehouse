"""review.yml skips an Ashigaru release PR, and only that.

The skip is run here, extracted from the workflow between its marker
comments. A release PR from the app's own branch in the same repository
exits 0 before anything is reviewed; any other PR falls through to the review.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REVIEW = Path(__file__).parent.parent / ".github" / "workflows" / "review.yml"
APP = "ashigaru-crunchtools[bot]"
REPO = "crunchtools/example"


def _skipped(tmp_path: Path, ref: str, head_repo: str, author: str) -> bool:
    block = re.search(
        r"# --- release skip.*?\n(.*?)# --- end release skip ---", REVIEW.read_text(), re.S
    )
    assert block, "release-skip markers missing from review.yml"
    summary = tmp_path / "summary.md"
    script = f"set -euo pipefail\n{block.group(1)}\nexit 3\n"
    env = {
        **os.environ,
        "HEAD_REF": ref,
        "HEAD_REPO": head_repo,
        "PR_AUTHOR": author,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    done = subprocess.run(
        ["bash", "-c", script], check=False, env=env, capture_output=True, text=True
    )
    assert done.returncode in (0, 3), done.stderr
    return done.returncode == 0


def test_ashigaru_release_pr_is_skipped(tmp_path: Path) -> None:
    assert _skipped(tmp_path, "ashigaru/release-3.5.0", REPO, APP)
    assert "not reviewed" in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize(
    ("ref", "head_repo", "author"),
    [
        ("ashigaru/issue-12", REPO, APP),
        ("ashigaru/release-3.5.0", "someone/fork", APP),
        ("ashigaru/release-3.5.0", REPO, "someone"),
        ("feature/ashigaru/release-1.0.0", REPO, APP),
        ("main", REPO, "dependabot[bot]"),
    ],
)
def test_other_prs_are_reviewed(tmp_path: Path, ref: str, head_repo: str, author: str) -> None:
    assert not _skipped(tmp_path, ref, head_repo, author)
