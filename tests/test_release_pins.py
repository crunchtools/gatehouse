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
    match = re.search(r'default: "quay\.io/crunchtools/gatehouse:([^"]+)"',
                      REVIEW_WORKFLOW.read_text())
    assert match, "review.yml has no default gatehouse image"
    assert match.group(1) == __version__
