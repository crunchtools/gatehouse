"""The reusable review workflow must run the image of the release that pins it.

`review.yml`'s default image is a hard-coded tag, bumped by hand. It sat at
0.4.0 through three releases, so every `review.yml@v0.7.0` caller reviewed
with a 0.4.0 container.
"""

from __future__ import annotations

import re
from pathlib import Path

from gatehouse import __version__

REVIEW_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "review.yml"


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


def test_examples_pin_this_release():
    """Every example adopters copy pins the release it ships in.

    examples/gourmand.yml stayed at v0.5.0 for ten releases while the other
    examples were bumped by hand, so new adopters started out stale.
    """
    examples = Path(__file__).parent.parent / "examples"
    pins = {
        (path.name, ref)
        for path in examples.glob("*.y*ml")
        for ref in re.findall(r"crunchtools/gatehouse/\S+@v(\S+)", path.read_text())
    }
    assert pins, "no gatehouse pins found in examples/"
    assert {ref for _, ref in pins} == {__version__}, sorted(pins)
