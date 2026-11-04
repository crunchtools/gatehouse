"""The finding ledger: findings already judged, so a run stops raising them.

`gatehouse-findings.toml` lives at the reviewed repository's root and is
committed with the code it describes. Gatehouse only ever reads it — the
pre-commit hook mounts the repository read-only, and a reviewer that holds a
diff it did not write has no business writing to the tree. When a run has
findings it prints entries ready to paste, and whoever judged the finding
fills in the reason and stages the file.

An entry suppresses its finding while the code it quotes is still there. Once
that code is gone the entry is stale and is printed for deletion, so a
suppression dies with the lines it covered rather than outliving them and
hiding the next defect in the same place.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from gatehouse.consensus import quotes_overlap

if TYPE_CHECKING:
    from collections.abc import Callable

LEDGER_FILE = "gatehouse-findings.toml"
ENTRIES_KEY = "findings"

NOT_A_BUG = "not-a-bug"
DEFERRED = "deferred"
STATUSES = (NOT_A_BUG, DEFERRED)

# The same bar the triage check sets for a reply on a pull request: a reason
# short enough to be a shrug does not answer a finding.
MIN_REASON_CHARS = 20


@dataclass(frozen=True)
class Entry:
    """One judged finding.

    ``evidence`` is squashed, as the tally squashes a finding's quote, so the
    two match the same way clustering does: by containment, not by equality.
    ``quote`` keeps the text as written, for printing an entry that has gone
    stale.
    """

    agent: str
    category: str
    file: str
    evidence: str
    quote: str
    status: str
    reason: str

    def covers(self, agent_slug: str, finding: dict[str, Any], evidence: str) -> bool:
        """True when this entry is the judgement on that finding."""
        if self.agent != agent_slug or self.file != str(finding.get("file", "")):
            return False
        if self.category != str(finding.get("category", "")):
            return False
        return bool(evidence) and quotes_overlap(evidence, self.evidence)


def parse_ledger(text: str | None, squash: Callable[[str], str]) -> tuple[list[Entry], list[str]]:
    """Read the ledger, returning its entries and a complaint about each one skipped.

    A malformed ledger never fails a review: its entries only ever make the
    review say less, and in CI it comes from the pull request's own head, which
    on a fork is not ours. Anything unreadable is skipped and named, so a typo
    is loud without being fatal.
    """
    if text is None or not text.strip():
        return [], []
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [f"{LEDGER_FILE} is not valid TOML: {exc}"]

    raw = document.get(ENTRIES_KEY)
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"{LEDGER_FILE}: [[{ENTRIES_KEY}]] must be a list of entries"]

    entries: list[Entry] = []
    problems: list[str] = []
    for index, item in enumerate(raw, start=1):
        entry, problem = _entry(item, index, squash)
        if entry is not None:
            entries.append(entry)
        if problem is not None:
            problems.append(problem)
    return entries, problems


def _strings(item: dict[str, Any], where: str) -> tuple[dict[str, str] | None, str | None]:
    """Every required field as a stripped string, or None and what was wrong.

    The printed entry leaves reason empty on purpose, so "is empty" is the
    message an author sees when they stage one before filling it in.
    """
    fields: dict[str, str] = {}
    for key in ("agent", "category", "file", "evidence", "status", "reason"):
        if key not in item:
            return None, f"{where}: {key} is missing"
        value = item[key]
        if not isinstance(value, str):
            return None, f"{where}: {key} must be a string, not {type(value).__name__}"
        if not value.strip():
            return None, f"{where}: {key} is empty"
        fields[key] = value.strip()
    return fields, None


def _unjudged(fields: dict[str, str], where: str) -> str | None:
    """Why these fields are not a judgement, or None when they are one."""
    if fields["status"] not in STATUSES:
        return f"{where}: status {fields['status']!r} is not one of {', '.join(STATUSES)}"
    if len(fields["reason"]) < MIN_REASON_CHARS:
        return (
            f"{where}: reason is {len(fields['reason'])} characters, "
            f"under the {MIN_REASON_CHARS} a judgement needs"
        )
    if fields["status"] == DEFERRED and "://" not in fields["reason"]:
        return f"{where}: a deferred finding's reason must link the issue tracking it"
    return None


def _entry(item: Any, index: int, squash: Callable[[str], str]) -> tuple[Entry | None, str | None]:
    """One ledger entry, or None and why it was skipped."""
    where = f"{LEDGER_FILE} entry {index}"
    if not isinstance(item, dict):
        return None, f"{where}: not a table"

    fields, problem = _strings(item, where)
    if fields is None:
        return None, problem

    problem = _unjudged(fields, where)
    if problem is not None:
        return None, problem

    squashed = squash(fields["evidence"])
    if not squashed:
        return None, f"{where}: evidence has nothing quotable in it"

    return (
        Entry(
            agent=fields["agent"],
            category=fields["category"],
            file=fields["file"],
            evidence=squashed,
            quote=fields["evidence"],
            status=fields["status"],
            reason=fields["reason"],
        ),
        None,
    )


def judgement(
    entries: list[Entry], agent_slug: str, finding: dict[str, Any], evidence: str
) -> Entry | None:
    """The entry that judged this finding, if one did."""
    for entry in entries:
        if entry.covers(agent_slug, finding, evidence):
            return entry
    return None


def stale(entries: list[Entry], reviewed: dict[str, str]) -> list[Entry]:
    """The entries whose quoted code is gone from the file they name.

    ``reviewed`` maps a path this review showed to that file's content with
    whitespace stripped. An entry is judged only against its OWN file: the
    same line can appear in another changed file, which would otherwise keep a
    dead entry alive, and a narrow diff that never showed the file says
    nothing either way. An entry whose file is absent is left alone.
    """
    return [
        entry
        for entry in entries
        if entry.file in reviewed and entry.evidence not in reviewed[entry.file]
    ]


def render_entry(agent_slug: str, finding: dict[str, Any]) -> str:
    """A ledger entry for this finding, ready to paste with the reason filled in."""
    return "\n".join(
        (
            f"[[{ENTRIES_KEY}]]",
            f'agent = "{agent_slug}"',
            f'category = "{_toml_string(str(finding.get("category", "")))}"',
            f'file = "{_toml_string(str(finding.get("file", "")))}"',
            f'evidence = "{_toml_string(str(finding.get("evidence", "")))}"',
            f'status = "{NOT_A_BUG}"',
            'reason = ""',
        )
    )


def _toml_string(value: str) -> str:
    """Escape a value for a TOML basic string, newlines included."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def entries_added(head: list[Entry], base: list[Entry]) -> list[Entry]:
    """The entries this branch adds, which nobody has reviewed yet.

    An entry on the base branch was reviewed when it was merged. One that
    appears only on the head is this pull request asking to silence a finding,
    and that is a change a maintainer should have to answer for.
    """
    known = {key(entry) for entry in base}
    return [entry for entry in head if key(entry) not in known]


def key(entry: Entry) -> tuple[str, str, str, str]:
    """What makes two ledger entries the same entry."""
    return (entry.agent, entry.category, entry.file, entry.evidence)


def entry_line(text: str, entry: Entry) -> int:
    """The line in the ledger file where this entry's evidence is written.

    The quote is matched in the form TOML stores it, escapes included, so an
    entry quoting code with a double quote or a backslash in it still finds
    its line. Zero when it cannot be found, which keeps the finding out of the
    inline comments rather than anchoring it to the wrong line.
    """
    first = entry.quote.splitlines()[0] if entry.quote.splitlines() else entry.quote
    needle = _toml_string(first)
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("evidence") and needle in line:
            return number
    return 0


def suppression_finding(entry: Entry, line: int) -> dict[str, Any]:
    """A new ledger entry, as a finding someone has to answer on the pull request.

    Medium severity because the triage check requires an answer to one from
    any agent: a suppression is the single way to silence this gate, so it
    must not be able to land without a maintainer saying yes to it.
    """
    return {
        "file": LEDGER_FILE,
        "lineStart": line,
        "lineEnd": line,
        "severity": "medium",
        "category": "quality",
        "description": (
            f"This branch silences a {entry.agent} finding in {entry.file} "
            f"as {entry.status}: {entry.reason}"
        ),
        "suggestion": (
            "Confirm the judgement is right, or drop the entry and fix the finding. "
            "The entry stops applying on its own once the code it quotes is gone."
        ),
        "evidence": entry.quote,
        "confidence": 100,
    }
