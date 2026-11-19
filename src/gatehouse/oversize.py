"""Keep a diff small enough to send: elide large deletions, refuse what is still too big.

Every agent sample is one paid call carrying the whole diff, so a diff no
model can take must be stopped before the first call, not discovered by
twenty-four HTTP 400s. A deleted file's lines reach nothing in the new tree;
past DELETED_FILE_BYTES only its header and a one-line note are sent. A small
deletion is still sent whole, so a removed protection stays visible.
"""

from __future__ import annotations

import itertools

from gatehouse.diffview import OMITTED_PREFIX
from gatehouse.ignore import diff_sections, not_hunk, segment_paths

MAX_DIFF_BYTES = 1024 * 1024
MAX_DIFF_BYTES_ENV = "GATEHOUSE_MAX_DIFF_BYTES"
DELETED_FILE_BYTES = 64 * 1024
# Files named in a refusal, largest first.
LARGEST_SHOWN = 5


def section_bytes(section: list[str]) -> int:
    """Bytes in a section, measured line by line rather than by joining a copy of it."""
    return sum(len(line.encode()) for line in section)


def _name(section: list[str]) -> str:
    return " -> ".join(sorted(segment_paths(section))) or section[0].rstrip("\n")


def _elided(section: list[str], limit: int) -> list[str] | None:
    """A deleted file's section over ``limit`` bytes as its header and a note; else None."""
    if not section[0].startswith("diff --git "):
        return None
    header = list(itertools.takewhile(not_hunk, section))
    if not any(line.startswith(("deleted file mode", "+++ /dev/null")) for line in header):
        return None
    size = section_bytes(section)
    if size <= limit:
        return None
    removed = sum(1 for line in section[len(header) :] if line.startswith("-"))
    return [*header, f"{OMITTED_PREFIX}{removed} deleted line(s), {size} bytes\n"]


def elide_deletions(diff: str, limit: int = DELETED_FILE_BYTES) -> tuple[str, list[str]]:
    """Replace the hunks of each deleted file over ``limit`` bytes with a note.

    Returns the diff and the paths elided. The header is kept, so the file
    still reads as deleted, and the note replaces the hunks, so the result is
    small enough that eliding it again changes nothing.
    """
    sections = [(section, _elided(section, limit)) for section in diff_sections(diff)]
    text = "".join(line for section, short in sections for line in short or section)
    return text, [_name(section) for section, short in sections if short is not None]


def oversize_message(diff: str, limit: int = MAX_DIFF_BYTES) -> str | None:
    """Why ``diff`` is too large to send, naming the limit and the largest files; else None."""
    size = len(diff.encode())
    if size <= limit:
        return None
    files = sorted(
        (
            (section_bytes(section), _name(section))
            for section in diff_sections(diff)
            if section[0].startswith("diff --git ")
        ),
        reverse=True,
    )[:LARGEST_SHOWN]
    largest = "".join(f"\n  {bytes_:>12} bytes  {name}" for bytes_, name in files)
    return (
        f"diff is {size} bytes, over the {limit}-byte limit ({MAX_DIFF_BYTES_ENV}, "
        f"or the review workflow's max_diff_bytes input); "
        f"no model was called. Add generated or data files to .gatehouse-ignore, "
        f"or split the change. Largest files:{largest}"
    )
