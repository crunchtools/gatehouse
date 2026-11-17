"""review.yml's skips: which PRs get no LLM review, and what a skip leaves.

The skips are run here, extracted from the workflow between their marker
comments, against a fake `gh` that serves fixtures and records what is
posted. A skipped PR exits 0 before anything is reviewed; any other PR falls
through to the review (exit 3 here).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gatehouse.github import REVIEW_FAILED_MARKER, REVIEW_MARKER

REVIEW = Path(__file__).parent.parent / ".github" / "workflows" / "review.yml"
APP = "ashigaru-crunchtools[bot]"
BOT = "github-actions[bot]"
REPO = "crunchtools/example"
BASE = "b" * 40
HEAD = "c" * 40
SEEN = "a" * 40

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("git") is None, reason="jq and git needed"
)

# Serves `gh api` from the fixture directory in $FAKE_GH: reviews.json,
# files.json, pr.json, and for a compare diffs/<head sha>.diff (asked for as a
# diff) or commits.json. Missing fixture, a file named `fail`, or one named
# after the endpoint's last segment -> exit 1. A POST appends its -f fields
# to posts.jsonl.
FAKE_GH = """\
import json, os, subprocess, sys
from pathlib import Path

d = Path(os.environ["FAKE_GH"])
args, jq, post, fields, endpoint, as_diff = sys.argv[2:], None, False, {}, "", False
i = 0
while i < len(args):
    a = args[i]
    if a == "--jq":
        jq = args[i + 1]
    elif a == "-X":
        post = args[i + 1] == "POST"
    elif a == "-f":
        k, _, v = args[i + 1].partition("=")
        fields[k] = v
    elif a == "-H":
        as_diff = "diff" in args[i + 1]
    elif a.startswith("-"):
        i += 1
        continue
    else:
        endpoint = a
        i += 1
        continue
    i += 2
if (d / "fail").exists():
    sys.exit(1)
if post:
    if (d / "fail-post").exists():
        sys.exit(1)
    with open(d / "posts.jsonl", "a") as f:
        f.write(json.dumps({"endpoint": endpoint, **fields}) + "\\n")
    sys.exit(0)
path = endpoint.split("?")[0]
if (d / ("fail-" + path.rsplit("/", 1)[-1])).exists():
    sys.exit(1)
if "/compare/" in path and as_diff:
    src = d / "diffs" / (path.split("...")[1] + ".diff")
elif "/compare/" in path:
    src = d / "commits.json"
elif path.endswith("/reviews"):
    src = d / "reviews.json"
elif path.endswith("/files"):
    src = d / "files.json"
else:
    src = d / "pr.json"
if not src.exists():
    sys.exit(1)
text = src.read_text()
if jq:
    done = subprocess.run(["jq", "-r", jq], input=text, capture_output=True, text=True, check=True)
    text = done.stdout
sys.stdout.write(text)
"""

DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,3 +10,4 @@ def main():
     a = 1
     b = 2
+    c = 3
     return a
"""
# The same change on a newer base: other blob ids and line numbers.
REBASED = DIFF.replace("1111111..2222222", "3333333..4444444").replace("-10,3 +10,4", "-14,3 +14,4")
OTHER = DIFF.replace("c = 3", "c = 4")


def _review(rid: int, body: str, sha: str = SEEN, user: str = BOT) -> dict:
    return {
        "id": rid,
        "user": {"login": user},
        "body": body,
        "commit_id": sha,
        "html_url": f"https://example.test/r/{rid}",
    }


# A pin bump as Dependabot writes one, per file that only admits pin lines.
PATCHES = {
    ".github/workflows/ci.yml": (
        "@@ -12,7 +12,7 @@ jobs:\n     steps:\n"
        "-      - uses: actions/checkout@abc123 # v4.1.0\n"
        "+      - uses: actions/checkout@def456 # v4.2.0\n"
        "       with:"
    ),
    "Containerfile": (
        "@@ -1,3 +1,3 @@\n-FROM quay.io/x/python:3.13\n+FROM quay.io/x/python:3.14\n ARG A=1"
    ),
}

CLEAN = f"Gatehouse found no issues.\n\n{REVIEW_MARKER}"


class Run:
    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path / "gh"
        (self.dir / "diffs").mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!{sys.executable}\n{FAKE_GH}")
        gh.chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        self.summary = tmp_path / "summary.md"
        self.reviews([])
        self.orphans()

    def reviews(self, reviews: list[dict]) -> None:
        (self.dir / "reviews.json").write_text(json.dumps(reviews))

    def files(
        self,
        *names: str,
        changed: int | None = None,
        renamed_from: str = "",
        patch: str | None = None,
    ) -> None:
        files = [
            {"filename": n, "status": "modified", "patch": PATCHES.get(n, "@@ -1 +1 @@\n-a\n+b")}
            for n in names
        ]
        if patch is not None:
            files[0]["patch"] = patch
        if renamed_from:
            files[0].update(status="renamed", previous_filename=renamed_from)
        (self.dir / "files.json").write_text(json.dumps(files))
        count = len(names) if changed is None else changed
        (self.dir / "pr.json").write_text(json.dumps({"changed_files": count}))

    def diff(self, sha: str, text: str) -> None:
        (self.dir / "diffs" / f"{sha}.diff").write_text(text)

    def orphans(self, *shas: str) -> None:
        """The commits a compare of the head with an earlier head lists."""
        commits = {"commits": [{"sha": sha} for sha in shas]}
        (self.dir / "commits.json").write_text(json.dumps(commits))

    @property
    def posts(self) -> list[dict]:
        log = self.dir / "posts.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def skipped(self, ref: str = "feature", head_repo: str = REPO, author: str = "someone") -> bool:
        block = re.search(r"# --- skips.*?\n(.*?)# --- end skips ---", REVIEW.read_text(), re.S)
        assert block, "skip markers missing from review.yml"
        script = f"set -euo pipefail\n{block.group(1)}\nexit 3\n"
        env = {
            **os.environ,
            "PATH": self.path,
            "FAKE_GH": str(self.dir),
            "HEAD_REF": ref,
            "HEAD_REPO": head_repo,
            "PR_AUTHOR": author,
            "PR_NUMBER": "7",
            "BASE_REF": BASE,
            "HEAD_SHA": HEAD,
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }
        done = subprocess.run(
            ["bash", "-c", script], check=False, env=env, capture_output=True, text=True
        )
        assert done.returncode in (0, 3), done.stderr
        return done.returncode == 0


@pytest.fixture
def run(tmp_path: Path) -> Run:
    return Run(tmp_path)


# --- 1) Ashigaru release PRs ---


def test_ashigaru_release_pr_is_skipped(run: Run) -> None:
    assert run.skipped("ashigaru/release-3.5.0", REPO, APP)
    assert "not reviewed" in run.summary.read_text()
    assert run.posts == []


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
def test_other_prs_are_reviewed(run: Run, ref: str, head_repo: str, author: str) -> None:
    run.files("src/app.py")
    assert not run.skipped(ref, head_repo, author)


# --- 2) Bot PRs that only move pins ---

PINS = (
    "pyproject.toml",
    "uv.lock",
    "requirements-dev.txt",
    "server/requirements.txt",
    "Containerfile",
    "IMAGE_TAG",
    "CHANGELOG.md",
    ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
    ".github/dependabot.yml",
)


@pytest.mark.parametrize("author", ["dependabot[bot]", "crunchtools-ci[bot]", APP])
def test_bot_pin_pr_is_skipped_and_says_so(run: Run, author: str) -> None:
    run.files(*PINS)
    assert run.skipped("chore/python-sdlc", REPO, author)
    [post] = run.posts
    assert post["endpoint"] == f"repos/{REPO}/pulls/7/reviews"
    assert post["commit_id"] == HEAD
    assert post["body"].startswith("Gatehouse skipped this review")
    assert REVIEW_MARKER not in post["body"], "a skip must not become an incremental base"
    assert "No model was called" in run.summary.read_text()


def test_bot_pin_skip_is_said_once_per_pr(run: Run) -> None:
    run.files("uv.lock")
    run.reviews(
        [_review(1, "Gatehouse skipped this review: x\n\n<!-- gatehouse skipped: pins -->")]
    )
    assert run.skipped(author="dependabot[bot]")
    assert run.posts == []


def test_bot_pin_skip_clears_a_failed_review(run: Run) -> None:
    """A newest failed review would hold triage; the skip review replaces it."""
    run.files("uv.lock")
    run.reviews([_review(1, f"Gatehouse could not review this PR: x\n\n{REVIEW_FAILED_MARKER}")])
    assert run.skipped(author="dependabot[bot]")
    assert len(run.posts) == 1


def test_human_pin_pr_is_reviewed(run: Run) -> None:
    run.files("uv.lock", "pyproject.toml")
    assert not run.skipped(author="fatherlinux")
    assert run.posts == []


@pytest.mark.parametrize(
    "names",
    [
        ("uv.lock", "src/gatehouse/__init__.py"),
        ("Containerfile.sh",),
        (".github/workflows/scripts/deploy.sh",),
        ("docs/CHANGELOG.md.j2",),
    ],
)
def test_bot_pr_touching_code_is_reviewed(run: Run, names: tuple[str, ...]) -> None:
    run.files(*names)
    assert not run.skipped(author="dependabot[bot]")


@pytest.mark.parametrize(
    ("name", "patch"),
    [
        (".github/workflows/ci.yml", "@@ -5 +5 @@\n-        run: make\n+        run: curl x | sh"),
        (".github/workflows/ci.yml", "@@ -5 +5,2 @@\n       - uses: a/b@v1\n+        env:"),
        (".github/workflows/ci.yml", None),
        ("Containerfile", "@@ -3 +3 @@\n-RUN make\n+RUN curl x | sh"),
    ],
)
def test_bot_change_beyond_pins_in_a_workflow_or_containerfile_is_reviewed(
    run: Run, name: str, patch: str | None
) -> None:
    run.files(name, patch=patch)
    if patch is None:
        files = json.loads((run.dir / "files.json").read_text())
        del files[0]["patch"]
        (run.dir / "files.json").write_text(json.dumps(files))
    assert not run.skipped(author="ashigaru-crunchtools[bot]")


def test_bot_pr_from_a_fork_is_reviewed(run: Run) -> None:
    run.files("uv.lock")
    assert not run.skipped(head_repo="mallory/example", author="dependabot[bot]")


def test_bot_rename_out_of_code_is_reviewed(run: Run) -> None:
    run.files("pyproject.toml", renamed_from="src/setup.py")
    assert not run.skipped(author="crunchtools-ci[bot]")


def test_bot_pr_with_unlisted_files_is_reviewed(run: Run) -> None:
    run.files("uv.lock", changed=4000)
    assert not run.skipped(author="dependabot[bot]")


def test_bot_pr_is_reviewed_when_the_api_fails(run: Run) -> None:
    run.files("uv.lock")
    (run.dir / "fail").touch()
    assert not run.skipped(author="dependabot[bot]")


def test_nothing_is_skipped_without_the_earlier_reviews(run: Run) -> None:
    """Unread, they might hold the skip already said, or a reviewed head."""
    run.files("uv.lock")
    run.diff(SEEN, DIFF)
    run.diff(HEAD, REBASED)
    (run.dir / "fail-reviews").touch()
    assert not run.skipped(author="dependabot[bot]")
    assert not run.skipped()


# --- 3) Heads whose patch was already reviewed ---


def test_rebase_with_the_same_patch_is_skipped(run: Run) -> None:
    fix = "e" * 40
    run.reviews([_review(1, CLEAN)])
    run.diff(SEEN, DIFF)
    run.diff(HEAD, REBASED)
    run.orphans(fix, SEEN)
    assert run.skipped()
    [post] = run.posts
    assert post["commit_id"] == HEAD
    assert post["body"].startswith("Gatehouse skipped this review: the patch is unchanged")
    assert "https://example.test/r/1" in post["body"]
    assert f"<!-- gatehouse carried {fix} {SEEN} -->" in post["body"]
    assert REVIEW_MARKER in post["body"], "the next push reviews only what follows"


def test_changed_patch_is_reviewed(run: Run) -> None:
    run.reviews([_review(1, CLEAN)])
    run.diff(SEEN, DIFF)
    run.diff(HEAD, OTHER)
    assert not run.skipped()
    assert run.posts == []


def test_whitespace_change_is_reviewed(run: Run) -> None:
    """Indentation is meaning in Python and YAML: a re-indent is a new patch."""
    run.reviews([_review(1, CLEAN)])
    run.diff(SEEN, DIFF)
    run.diff(HEAD, DIFF.replace("+    c = 3", "+c = 3"))
    assert not run.skipped()


def test_matches_any_recent_reviewed_head(run: Run) -> None:
    newer = "d" * 40
    run.reviews([_review(1, CLEAN), _review(2, CLEAN, sha=newer)])
    run.diff(SEEN, DIFF)
    run.diff(newer, OTHER)
    run.diff(HEAD, REBASED)
    run.orphans(SEEN)
    assert run.skipped()
    assert f"carried {SEEN} -->" in run.posts[0]["body"]


@pytest.mark.parametrize(
    "review",
    [
        _review(1, f"Gatehouse could not review this PR: x\n\n{REVIEW_FAILED_MARKER}"),
        _review(1, "Gatehouse found 2 issues, 3 of 8 agents did not finish."),
        _review(1, CLEAN, user="mallory"),
        _review(1, f"LGTM {REVIEW_MARKER}"),
    ],
)
def test_only_complete_gatehouse_reviews_count(run: Run, review: dict) -> None:
    run.reviews([review])
    run.diff(SEEN, DIFF)
    run.diff(HEAD, REBASED)
    assert not run.skipped()


def test_rebase_is_reviewed_when_its_orphans_cannot_be_listed(run: Run) -> None:
    """Unlisted, the old head's findings would be dropped with nothing re-reading them."""
    run.reviews([_review(1, CLEAN)])
    run.diff(SEEN, DIFF)
    run.diff(HEAD, REBASED)
    (run.dir / "commits.json").unlink()
    assert not run.skipped()
    assert run.posts == []


@pytest.mark.parametrize("author", ["dependabot[bot]", "someone"])
def test_a_skip_that_cannot_be_posted_is_reviewed(run: Run, author: str) -> None:
    """Unsaid, a skip would be silent; triage might also hold a failed review."""
    run.files("uv.lock")
    run.reviews([_review(1, CLEAN)])
    run.diff(SEEN, DIFF)
    run.diff(HEAD, REBASED)
    (run.dir / "fail-post").touch()
    assert not run.skipped(author=author)
    assert "No model was called" not in (run.summary.read_text() if run.summary.exists() else "")


def test_unreadable_diff_is_reviewed(run: Run) -> None:
    run.reviews([_review(1, CLEAN)])
    run.diff(SEEN, DIFF)
    assert not run.skipped()


def test_first_review_is_never_skipped(run: Run) -> None:
    run.diff(HEAD, DIFF)
    assert not run.skipped()
