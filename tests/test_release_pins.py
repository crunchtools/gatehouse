"""The reusable review workflow must run the image of the release it ships in.

`review.yml`'s default image is a hard-coded tag, bumped by hand. It sat at
0.4.0 through three releases, so every `review.yml@v0.7.0` caller reviewed
with a 0.4.0 container.
"""

from __future__ import annotations

import re
from pathlib import Path

from gatehouse import __version__

REVIEW_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "review.yml"
CONTAINER_WORKFLOW = REVIEW_WORKFLOW.with_name("container.yml")


def test_review_workflow_default_image_matches_package_version():
    match = re.search(
        r'default: "quay\.io/crunchtools/gatehouse:([^"]+)"', REVIEW_WORKFLOW.read_text()
    )
    assert match, "review.yml has no default gatehouse image"
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


def test_the_major_tag_moves_only_after_both_images_are_pushed():
    """`@v0` callers run review.yml's default image, so the tag must not get there first."""
    job = CONTAINER_WORKFLOW.read_text().split("\n  major-tag:\n")[1]
    assert "    needs: [build-and-push-quay, build-and-push-ghcr]\n" in job
    assert "    if: github.ref_type == 'tag'\n" in job
    assert "    permissions:\n      contents: write\n" in job
