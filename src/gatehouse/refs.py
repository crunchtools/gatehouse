"""Disprove findings that call an action ref nonexistent.

A model's knowledge stops at a cutoff, so every Dependabot bump to a newer
major (checkout@v7, setup-python@v7) drew "this tag does not exist". Telling
the model not to say so (0.16.0) did not hold. GitHub knows which refs exist,
so ask it: a finding whose only claim is a missing ref that resolves is
dropped. An unanswered lookup keeps the finding.
"""

from __future__ import annotations

import os
import re
import sys
from typing import TYPE_CHECKING, Any

import httpx

from gatehouse.github import GITHUB_API_URL

if TYPE_CHECKING:
    from collections.abc import Callable

    Resolver = Callable[[str, str, str], bool | None]

_MISSING_RE = re.compile(
    r"\b(?:does|do|did)(?: not|n't) (?:yet )?exist"
    r"|\bnon-?existent\b"
    r"|\bno such (?:tag|version|release|ref)\b"
    r"|\bnot (?:yet )?(?:been )?(?:released|published)\b"
    r"|\b(?:invalid|unknown|unreleased|fictional) (?:tag|version|release|ref)\b"
    r"|\bnot an? (?:valid |real |existing )?(?:tag|version|release)\b",
    re.I,
)
# owner/repo[/path]@ref, as a `uses:` line spells an action or reusable workflow.
_USES_RE = re.compile(
    r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)(?:/[A-Za-z0-9_./-]+)?@([A-Za-z0-9_./-]+)"
)
# A sentence that is about something on the action, not the ref itself:
# "input `foo` does not exist on actions/checkout@v7" stays a finding.
_OTHER_SUBJECT_RE = re.compile(
    r"\b(?:inputs?|outputs?|parameters?|arguments?|options?|keys?|fields?|"
    r"files?|paths?|secrets?|permissions?)\b",
    re.I,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TIMEOUT = 10.0


def _claim_sentences(finding: dict[str, Any]) -> list[str]:
    text = " ".join(str(finding.get(k, "")) for k in ("description", "suggestion"))
    return [
        s
        for s in _SENTENCE_RE.split(text)
        if _MISSING_RE.search(s) and not _OTHER_SUBJECT_RE.search(s)
    ]


def cited_refs(finding: dict[str, Any]) -> set[tuple[str, str, str]]:
    """The owner/repo@ref refs a missing-ref finding rests on; empty if it claims none.

    A ref counts only if a sentence claiming something missing names it, as
    owner/repo@ref or as the bare ref, so a claim about anything else on the
    same line never puts the finding up for dropping.
    """
    claims = _claim_sentences(finding)
    if not claims:
        return set()
    text = " ".join(str(finding.get(k, "")) for k in ("evidence", "description"))
    cited = set()
    for o, r, raw in _USES_RE.findall(text):
        ref = raw.rstrip(".")
        bare = re.compile(rf"(?<![\w.-]){re.escape(ref)}(?![\w-])")
        if any(bare.search(s) for s in claims):
            cited.add((o, r, ref))
    return cited


def ref_exists(owner: str, repo: str, ref: str) -> bool | None:
    """True if GitHub resolves the ref to a commit, False if not, None if it could not say."""
    headers = {"Accept": "application/vnd.github.sha", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/commits/{ref}"
    try:
        status = httpx.get(url, headers=headers, timeout=_TIMEOUT).status_code
    except httpx.HTTPError as exc:
        print(f"Warning: could not resolve {owner}/{repo}@{ref}: {exc}", file=sys.stderr)
        return None
    if status == 200:
        return True
    return False if status in (404, 422) else None
