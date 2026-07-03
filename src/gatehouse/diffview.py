"""Convert unified diffs into a structured BEFORE/AFTER view for LLM review.

LLM agents reason poorly about raw unified diff syntax: they see +/- prefixed
text without reliably understanding which code exists after the change. That
caused issue #19 — hardening ADDED by a PR was flagged as a finding, and a
diff REMOVING a security control could slip by unnoticed.

This module parses ``git diff`` output and re-renders each hunk as an explicit
BEFORE block (old code, removed lines marked ``[-]``) and AFTER block (new
code with real file line numbers, added lines marked ``[+]``), plus a preamble
that tells agents how to judge the direction of a change. Removed lines are
preserved in BEFORE so deleted protections remain visible and flaggable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

DIFF_VIEW_PREAMBLE = """\
Each change below is shown as a BEFORE block (the old code, which no longer
exists) and an AFTER block (the new code as it now exists in the file).

How to read the blocks:
- AFTER lines marked [+] were added or changed by this change; unmarked
  AFTER lines are unchanged context.
- BEFORE lines marked [-] were removed or replaced by this change.
- Line numbers in the AFTER block are real line numbers in the current
  file. Use them for lineStart/lineEnd in findings.

Direction matters — judge every change by comparing BEFORE to AFTER:
- Review the AFTER code. Only the AFTER code exists now.
- If the AFTER block adds validation, sanitization, error handling, or
  other protections that BEFORE lacked, that is a fix being applied —
  NOT a finding. Do not flag it.
- If the BEFORE block contains validation, sanitization, authentication,
  access control, or other protections that are absent from the AFTER
  block, that protection was REMOVED — that IS a finding.
- Do not report findings on BEFORE-only code, except to flag removed
  protections as above."""


@dataclass
class Hunk:
    """One @@ hunk: old/new start lines and tagged (' ', '+', '-') lines."""

    old_start: int
    new_start: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FileDiff:
    """One file's changes within a diff."""

    path: str
    old_path: str | None = None
    status: str = "modified"
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)


def _strip_git_prefix(path: str) -> str:
    """Strip the a/ or b/ prefix git puts on diff paths."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _consume_hunk_line(hunk: Hunk, line: str) -> tuple[int, int]:
    """Add one body line to a hunk; return (old, new) lines consumed."""
    if line.startswith("\\"):
        return 0, 0  # "\ No newline at end of file"
    tag, text = (line[0], line[1:]) if line else (" ", "")
    consumed = {"+": (0, 1), "-": (1, 0), " ": (1, 1)}.get(tag)
    if consumed is None:
        raise ValueError(f"Unexpected line inside hunk: {line!r}")
    hunk.lines.append((tag, text))
    return consumed


def _start_hunk(line: str) -> tuple[Hunk, int, int]:
    """Parse a @@ header; return the hunk and its old/new line counts."""
    match = _HUNK_RE.match(line)
    if match is None:
        raise ValueError(f"Malformed hunk header: {line!r}")
    hunk = Hunk(old_start=int(match.group(1)), new_start=int(match.group(3)))
    return hunk, int(match.group(2) or "1"), int(match.group(4) or "1")


def _apply_metadata(current: FileDiff, line: str) -> None:
    """Apply a file-header line (mode, rename, ---/+++ paths) to a FileDiff."""
    if line.startswith("Binary files ") or line == "GIT binary patch":
        current.is_binary = True
    elif line.startswith("new file mode"):
        current.status = "added"
    elif line.startswith("deleted file mode"):
        current.status = "deleted"
    elif line.startswith("rename from "):
        current.old_path = line[len("rename from "):]
        current.status = "renamed"
    elif line.startswith("rename to "):
        current.path = line[len("rename to "):]
    elif line.startswith("--- "):
        path = _strip_git_prefix(line[4:])
        if path != "/dev/null" and current.old_path is None:
            current.old_path = path
    elif line.startswith("+++ "):
        path = _strip_git_prefix(line[4:])
        if path == "/dev/null":
            current.status = "deleted"
            current.path = current.old_path or ""
        else:
            current.path = path


def _parse_diff(diff: str) -> list[FileDiff]:
    """Parse unified diff text into FileDiff objects.

    Raises ValueError on input that does not look like a unified diff.
    """
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_remaining = 0
    new_remaining = 0

    for line in diff.splitlines():
        if hunk is not None and (old_remaining > 0 or new_remaining > 0):
            old_used, new_used = _consume_hunk_line(hunk, line)
            old_remaining -= old_used
            new_remaining -= new_used
            continue
        hunk = None

        if line.startswith("diff --git "):
            current = FileDiff(path="")
            files.append(current)
            continue
        if current is None:
            if line.startswith("--- "):
                # Plain (non-git) unified diff: file starts at ---/+++
                current = FileDiff(path="")
                files.append(current)
            else:
                continue

        if line.startswith("@@"):
            hunk, old_remaining, new_remaining = _start_hunk(line)
            current.hunks.append(hunk)
        else:
            _apply_metadata(current, line)

    parsed = [f for f in files if f.path or f.is_binary]
    if not parsed:
        raise ValueError("No parseable file changes found in diff")
    return parsed


def _render_block(
    label: str, start: int, lines: list[tuple[str, str]], marker: str
) -> str:
    """Render one BEFORE or AFTER block with line numbers and markers."""
    rendered: list[str] = [f"{label}:", "```"]
    number = start
    for tag, text in lines:
        mark = marker if tag == marker.strip("[]") else "   "
        rendered.append(f"{number:5d} {mark}| {text}")
        number += 1
    rendered.append("```")
    return "\n".join(rendered)


def _render_hunk(hunk: Hunk) -> str:
    """Render one hunk as BEFORE/AFTER blocks."""
    before = [(t, x) for t, x in hunk.lines if t in (" ", "-")]
    after = [(t, x) for t, x in hunk.lines if t in (" ", "+")]

    parts: list[str] = []
    if before and any(t == "-" for t, _ in before):
        parts.append(
            _render_block(
                "BEFORE (old code — no longer exists)",
                hunk.old_start, before, "[-]",
            )
        )
    if after:
        parts.append(
            _render_block(
                "AFTER (current code — real file line numbers)",
                hunk.new_start, after, "[+]",
            )
        )
    if not parts:
        return ""
    return "\n\n".join(parts)


def _file_header(file: FileDiff) -> str:
    """Render the per-file heading."""
    if file.status == "renamed" and file.old_path:
        return f"### {file.path} (renamed from {file.old_path})"
    labels = {"added": "new file", "deleted": "deleted"}
    return f"### {file.path} ({labels.get(file.status, 'modified')})"


def render_diff_view(diff: str) -> str | None:
    """Render a unified diff as a structured BEFORE/AFTER review view.

    Returns None if the diff cannot be parsed, so callers can fall back to
    passing the raw diff through unchanged.
    """
    try:
        files = _parse_diff(diff)
    except ValueError:
        return None

    sections: list[str] = [DIFF_VIEW_PREAMBLE]
    for file in files:
        parts: list[str] = [_file_header(file)]
        if file.is_binary:
            parts.append("Binary file changed — not reviewable as text.")
        elif file.status == "deleted":
            parts.append(
                "This file was DELETED. Flag any security controls, "
                "validation, or critical logic it contained that is not "
                "replaced elsewhere in this change."
            )
            parts.extend(p for h in file.hunks if (p := _render_hunk(h)))
        elif not file.hunks:
            parts.append("Metadata-only change (mode or rename), no content.")
        else:
            parts.extend(p for h in file.hunks if (p := _render_hunk(h)))
        sections.append("\n\n".join(parts))

    return "\n\n".join(sections)
