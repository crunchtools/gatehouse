"""`.gatehouse-ignore`: gitignore-syntax paths that are never sent for review.

Data files, fixtures and generated output cost tokens and draw no useful
findings. The ignore file itself is always reviewed, whatever it says.
"""

from __future__ import annotations

import itertools
import re
from typing import TYPE_CHECKING

import pathspec

if TYPE_CHECKING:
    from collections.abc import Iterator

IGNORE_FILE = ".gatehouse-ignore"

# A file section starts at its git header; `gh pr diff --patch` also puts a
# mail header before each commit. Hunk lines never match either: every body
# line starts with "+", "-", " " or "\\".
_SEGMENT_START = re.compile(r"^(?:diff --git |From [0-9a-f]{40} )")
# Header lines that name a path, and whether git prefixes it with a/ or b/.
_PATH_LINES = (
    ("--- ", True),
    ("+++ ", True),
    ("rename from ", False),
    ("rename to ", False),
    ("copy from ", False),
    ("copy to ", False),
)


# git's C-style quoting: octal bytes (\303\251) and the usual escapes.
_QUOTED = re.compile(r"(\\[0-7]{3}|\\.)|(.)", re.S)
_C_ESCAPES = {"a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r"}


def parse_ignore(text: str | None) -> pathspec.GitIgnoreSpec | None:
    """Compile ignore-file text, or None when there is nothing to ignore."""
    if not text or not text.strip():
        return None
    return pathspec.GitIgnoreSpec.from_lines(text.splitlines())


def _unquote(value: str) -> str:
    """Undo git's path quoting: "caf\\303\\251" is café, and a tab ends a path with spaces."""
    value = value.rstrip("\t")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        raw = bytearray()
        for escape, char in _QUOTED.findall(value[1:-1]):
            if not escape:
                raw += char.encode()
            elif escape[1:].isdigit():
                raw.append(int(escape[1:], 8))
            else:
                raw += _C_ESCAPES.get(escape[1], escape[1]).encode()
        return raw.decode("utf-8", errors="replace")
    return value


def _header_path(header: str) -> str | None:
    """The path in `diff --git a/P b/P` (quoted or not) when both sides agree."""
    rest = header[len("diff --git ") :]
    half = (len(rest) - 1) // 2
    if rest[half : half + 1] != " ":
        return None
    old, new = _unquote(rest[:half]), _unquote(rest[half + 1 :])
    if old.startswith("a/") and new.startswith("b/") and old[2:] == new[2:]:
        return old[2:]
    return None


def _not_hunk(line: str) -> bool:
    return not line.startswith("@@")


def _segment_paths(lines: list[str]) -> set[str]:
    """Every path a file section touches: old, new, rename and copy sides.

    Only the header counts: inside a hunk, a removed line "-- a/x" reads
    "--- a/x".
    """
    paths: set[str] = set()
    header = [line.rstrip("\n") for line in itertools.takewhile(_not_hunk, lines)]
    for line in header:
        for prefix, prefixed in _PATH_LINES:
            if not line.startswith(prefix):
                continue
            path = _unquote(line[len(prefix) :])
            if prefixed:
                if path == "/dev/null" or not path.startswith(("a/", "b/")):
                    continue
                path = path[2:]
            paths.add(path)
    if not paths:  # binary and mode-only changes carry no ---/+++ lines
        header_path = _header_path(header[0])
        if header_path is not None:
            paths.add(header_path)
    return paths


def _is_ignored(paths: set[str], spec: pathspec.GitIgnoreSpec) -> bool:
    """A section is dropped only when every path it touches is ignored."""
    if not paths or IGNORE_FILE in paths:
        return False
    return all(spec.match_file(path) for path in paths)


def _sections(diff: str) -> Iterator[list[str]]:
    """Yield the diff one section at a time: preamble, mail headers, files."""
    section: list[str] = []
    for line in diff.splitlines(keepends=True):
        if _SEGMENT_START.match(line) and section:
            yield section
            section = []
        section.append(line)
    if section:
        yield section


def filter_diff(diff: str, spec: pathspec.GitIgnoreSpec | None) -> tuple[str, list[str]]:
    """Drop ignored files' sections from a git diff.

    Returns the remaining diff and one entry per skipped file, sorted (a
    rename reads "old -> new"). Commit mail headers are kept alongside any
    file that survives; when none does, the diff is empty.
    """
    if spec is None:
        return diff, []

    kept: list[str] = []
    ignored: list[str] = []
    files_kept = 0
    for section in _sections(diff):
        if section[0].startswith("diff --git "):
            paths = _segment_paths(section)
            if _is_ignored(paths, spec):
                ignored.append(" -> ".join(sorted(paths)))
                continue
            files_kept += 1
        kept.extend(section)
    if ignored and not files_kept:
        return "", sorted(ignored)
    return "".join(kept), sorted(ignored)


def filter_listing(listing: str | None, spec: pathspec.GitIgnoreSpec | None) -> str | None:
    """Drop ignored paths from a `git ls-files` listing."""
    if listing is None or spec is None:
        return listing
    return "".join(
        line for line in listing.splitlines(keepends=True) if not spec.match_file(line.rstrip("\n"))
    )
