"""Version strings a release moves must agree with the package version."""

from __future__ import annotations

import re
from pathlib import Path

from gatehouse import __version__

CONTAINERFILE = Path(__file__).parent.parent / "Containerfile"


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
