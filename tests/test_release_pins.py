"""The reusable review workflow must run the image of the release it ships in.

`review.yml`'s default image used to be a hard-coded tag, bumped by hand. It sat
at 0.4.0 through three releases, so every `review.yml@v0.7.0` caller reviewed
with a 0.4.0 container, and Ashigaru's 0.24.1 release (which never edits
`.github/`) failed here with the tag still at 0.24.0. The workflow now reads the
version from its own commit, and these tests run that step.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from gatehouse import __version__

REVIEW_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "review.yml"
CONTAINER_WORKFLOW = REVIEW_WORKFLOW.with_name("container.yml")
CONTAINERFILE = Path(__file__).parent.parent / "Containerfile"
INIT = Path(__file__).parent.parent / "src" / "gatehouse" / "__init__.py"


def _resolve(tmp_path: Path, image: str, served: Path | None) -> subprocess.CompletedProcess[str]:
    """Run review.yml's resolve-image block with a stub `gh` that serves `served`."""
    block = re.search(
        r"# --- resolve image.*?\n(.*?)# --- end resolve image ---",
        REVIEW_WORKFLOW.read_text(),
        re.S,
    )
    assert block, "resolve-image markers missing from review.yml"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/bin/sh\necho "$@" > "{tmp_path}/gh-args"\n'
        + (f'cat "{served}"\n' if served else "exit 1\n")
    )
    gh.chmod(0o755)
    script = (
        f'set -euo pipefail\nfail_review() {{ echo "FAILED: $1"; exit 2; }}\n{block.group(1)}\n'
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "IMAGE": image,
        "WORKFLOW_REPO": "crunchtools/gatehouse",
        "WORKFLOW_SHA": "abc123",
    }
    return subprocess.run(
        ["bash", "-c", script], check=False, env=env, capture_output=True, text=True
    )


def test_review_workflow_has_no_hand_bumped_default_image():
    """The image is not typed into review.yml, so no release can leave it behind."""
    match = re.search(
        r'\n      image:\n(?:        .*\n)*?        default: "([^"]*)"', REVIEW_WORKFLOW.read_text()
    )
    assert match, "review.yml has no image input default"
    assert match.group(1) == ""


def test_review_workflow_default_image_matches_package_version(tmp_path: Path):
    """With no image input, the review runs the image of the workflow's own release."""
    done = _resolve(tmp_path, "", INIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"Reviewer image: quay.io/crunchtools/gatehouse:{__version__}" in done.stdout
    args = (tmp_path / "gh-args").read_text()
    assert "repos/crunchtools/gatehouse/contents/src/gatehouse/__init__.py?ref=abc123" in args


def test_review_workflow_image_input_overrides(tmp_path: Path):
    done = _resolve(tmp_path, "quay.io/example/gatehouse:9.9.9", INIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Reviewer image: quay.io/example/gatehouse:9.9.9" in done.stdout
    assert not (tmp_path / "gh-args").exists()


def test_review_workflow_fails_the_review_when_the_version_is_unreadable(tmp_path: Path):
    done = _resolve(tmp_path, "", None)
    assert done.returncode == 2
    assert "FAILED: could not read the Gatehouse version" in done.stdout


def test_containerfile_version_label_matches_package_version():
    """The constitution's VERSION check compares this label to the CHANGELOG.

    0.23.0 shipped with the label still at 0.22.0 and every local check passed:
    ruff, mypy, 528 tests, Gourmand and Gatehouse all missed it, and the
    constitution validator caught it in CI. A bumped version is now one test
    away from green instead of one CI round-trip.
    """
    match = re.search(r'version="([^"]+)"', CONTAINERFILE.read_text())
    assert match, "Containerfile has no version label"
    assert match.group(1) == __version__


def test_review_workflow_fetches_diff_with_escape_sequences():
    """gh pr diff refuses a diff with ESC bytes unless told; that failed #44."""
    text = REVIEW_WORKFLOW.read_text()
    assert "--allow-escape-sequences" in text
    assert '< "$diff_file"' in text
    assert "| docker run" not in text


def test_examples_call_the_major_tag():
    """Every example adopters copy calls the workflows at the major tag.

    examples/gourmand.yml stayed at v0.5.0 for ten releases while the other
    examples were bumped by hand, so new adopters started out stale. The
    major tag moves with each release, so an example cannot go stale and an
    adopter never bumps anything.
    """
    examples = Path(__file__).parent.parent / "examples"
    pins = {
        (path.name, ref)
        for path in examples.glob("*.y*ml")
        for ref in re.findall(r"crunchtools/gatehouse/\S+@v(\S+)", path.read_text())
    }
    assert pins, "no gatehouse calls found in examples/"
    assert {ref for _, ref in pins} == {__version__.split(".")[0]}, sorted(pins)


def test_the_major_tag_moves_only_after_the_image_is_promoted():
    """`@v0` callers run review.yml's default image, so the tag must not get there first."""
    job = CONTAINER_WORKFLOW.read_text().split("\n  major-tag:\n")[1]
    assert "    needs: [container]\n" in job
    assert "    if: github.ref_type == 'tag'\n" in job
    assert "    permissions:\n      contents: write\n" in job
