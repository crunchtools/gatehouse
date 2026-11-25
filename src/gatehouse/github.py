"""GitHub PR review integration via httpx."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx

from gatehouse.consensus import SAMPLES_KEY, VOTES_KEY
from gatehouse.ledger import LEDGER_FILE
from gatehouse.output import strip_ansi

if TYPE_CHECKING:
    from gatehouse.agents import Agent

GITHUB_API_URL = "https://api.github.com"


def detect_pr_context() -> tuple[str, int] | None:
    """Detect GitHub PR context from GHA environment variables.

    Returns (repo, pr_number) or None if not in a PR context. An unreadable
    or malformed event file prints a warning to stderr and is treated as
    no PR context.
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None

    github_ref = os.environ.get("GITHUB_REF", "")
    match = re.match(r"refs/pull/(\d+)/merge", github_ref)
    if match:
        return repo, int(match.group(1))

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path:
        try:
            with open(event_path) as f:
                event = json.load(f)
            pr_number = event.get("pull_request", {}).get("number")
            if isinstance(pr_number, int):
                return repo, pr_number
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(
                f"Warning: could not parse {event_path}: {exc}",
                file=sys.stderr,
            )

    return None


def fetch_repo_file(repo: str, ref: str, path: str, token: str) -> str | None:
    """Fetch one file's contents from a repo at a ref via the GitHub API.

    Used to load trusted context (styleguide, constitution) from the BASE repo
    of a pull request without checking anything out. The ref MUST be the trusted
    base (e.g. base-branch SHA), never the PR head — otherwise a fork could plant
    a prompt-injecting styleguide. Returns the file text, or None if absent
    or on error; network/HTTP errors also print a warning to stderr.
    """
    url = f"{GITHUB_API_URL}/repos/{repo}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = httpx.get(url, headers=headers, params={"ref": ref}, timeout=15.0)
    except httpx.HTTPError as exc:
        print(
            f"Warning: could not fetch {path} from {repo}@{ref}: {exc}",
            file=sys.stderr,
        )
        return None
    if response.is_success:
        return response.text
    return None


def _inert(text: str) -> str:
    """``text`` with no HTML comment opener or closer, so it cannot plant a hidden marker.

    Every Gatehouse marker is an HTML comment matched as a substring, so any
    PR-controlled string written into a review (a filename, a finding quoting
    the diff) passes through here first. A zero-width space splits each
    delimiter: it renders the same, in prose and in a code block, but no
    longer matches.
    """
    return text.replace("<!--", "<!\u200b--").replace("-->", "-\u200b->")


# The first line of every finding comment; triage.yml parses the same prefix.
FINDING_HEADER_RE = re.compile(r"^\*\*(CRITICAL|HIGH|MEDIUM|LOW)\*\* \(([^)]+)\):")


def _format_comment_body(agent: Agent, finding: dict[str, Any]) -> str:
    """Format a single finding as a PR review comment body.

    ANSI escape sequences and HTML comment delimiters are stripped from the
    finding's description, suggestion, and evidence before they are embedded
    in the comment. A
    hidden marker records the agent slug and confidence, so replies can be
    joined back to confidence when tuning the threshold.
    """
    severity = finding.get("severity", "low").upper()
    description = _inert(strip_ansi(finding.get("description", "")))
    suggestion = _inert(strip_ansi(finding.get("suggestion", "")))
    evidence = _inert(strip_ansi(finding.get("evidence", "")))

    parts = [f"**{severity}** ({agent.name}): {description}"]
    if suggestion:
        parts.append(f"\n**Suggestion:** {suggestion}")
    if evidence:
        parts.append(f"\n**Evidence:**\n```\n{evidence}\n```")
    confidence = int(finding.get("confidence", 0))
    votes = int(finding.get(VOTES_KEY, 1))
    samples = int(finding.get(SAMPLES_KEY, 1))
    parts.append(
        f"\n<!-- gatehouse agent={agent.slug} confidence={confidence} votes={votes}/{samples} -->"
    )
    return "\n".join(parts)


def fetch_answered_threads() -> list[tuple[str, str, int]]:
    """Return (agent name, path, line) for each answered finding on this PR.

    A finding is a top-level review comment posted by a bot account whose
    first line is a Gatehouse finding header; it is answered when someone
    other than its author replied. Requiring a bot author keeps a PR
    participant from forging a finding thread to suppress real ones.
    Outside a PR context, or on any API error, returns [] with a
    warning: suppression is an optimization, never a reason to fail.
    """
    comments = _fetch_pr_list("comments", "existing review threads")
    if not comments:
        return []

    # A deleted account comes back as "user": null.
    users = {c["id"]: c.get("user") or {} for c in comments}
    authors = {
        c["id"]: users[c["id"]].get("login") for c in comments if not c.get("in_reply_to_id")
    }
    answered = {
        c["in_reply_to_id"]
        for c in comments
        if c.get("in_reply_to_id") in authors
        and users[c["id"]].get("login") != authors[c["in_reply_to_id"]]
    }

    threads: list[tuple[str, str, int]] = []
    for c in comments:
        if c["id"] not in answered or users[c["id"]].get("type") != "Bot":
            continue
        match = FINDING_HEADER_RE.match(c.get("body", ""))
        line = c.get("line") or c.get("original_line")
        if match and c.get("path") and line:
            threads.append((match.group(2), c["path"], line))
    return threads


# Hidden in the body of every review in which all agents finished. The
# newest such review's commit is where an incremental re-review starts; an
# incomplete review must not be one, or what its failed agents missed would
# never be reviewed.
REVIEW_MARKER = "<!-- gatehouse review complete -->"
# Hidden in the body of a review that did not happen: fewer than half of its
# agents finished. triage.yml fails while the newest Gatehouse review on a PR
# carries it, and review.yml posts it when it cannot run the reviewer at all.
REVIEW_FAILED_MARKER = "<!-- gatehouse review failed -->"


def review_failed(failed_count: int, agent_count: int) -> bool:
    """True when fewer than half of the agents that ran finished."""
    total = max(agent_count, failed_count)
    return (total - failed_count) * 2 < total


def _get_pages(url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """GET every page of a GitHub list endpoint. Raises httpx.HTTPError or ValueError."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        response = httpx.get(
            url, headers=headers, params={"per_page": 100, "page": page}, timeout=15.0
        )
        response.raise_for_status()
        batch = response.json()
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def _pr_auth() -> tuple[tuple[str, int], str] | None:
    """((repo, PR number), token) for API calls on this PR, or None outside one."""
    context = detect_pr_context()
    token = os.environ.get("GITHUB_TOKEN", "")
    if context is None or not token:
        return None
    return context, token


def _fetch_pr_list(endpoint: str, what: str) -> list[dict[str, Any]]:
    """Every item of this PR's pulls/{n}/<endpoint> list.

    Outside a PR context, without a token, or on any API error (warning
    that `what` could not be fetched), returns [].
    """
    auth = _pr_auth()
    if auth is None:
        return []
    (repo, pr_number), token = auth
    url = f"{GITHUB_API_URL}/repos/{repo}/pulls/{pr_number}/{endpoint}"
    try:
        return _get_pages(url, _headers(token))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Warning: could not fetch {what}: {exc}", file=sys.stderr)
        return []


def fetch_last_reviewed_commit() -> str | None:
    """Return the commit of the newest complete Gatehouse review on this PR.

    A Gatehouse review is one posted by a bot account whose body carries
    REVIEW_MARKER and not REVIEW_FAILED_MARKER; requiring a bot keeps a PR
    participant from planting a base that would skip their commits, and
    excluding a failed review keeps a forged marker in it from making one.
    Outside a PR context, with no such review, or on any API error, returns
    None (with a warning on error): the caller then reviews the whole PR.
    """
    commits = [
        r["commit_id"]
        for r in _fetch_pr_list("reviews", "earlier reviews")
        if (r.get("user") or {}).get("type") == "Bot"
        and REVIEW_MARKER in (r.get("body") or "")
        and REVIEW_FAILED_MARKER not in (r.get("body") or "")
        and r.get("commit_id")
    ]
    return commits[-1] if commits else None


def fetch_compare(base: str, head: str) -> tuple[str, int] | None:
    """Return (unified diff, commit count) from base to head, when head descends from base.

    Fetched over the API, so nothing is checked out. Returns None when head
    does not strictly descend from base (a force-push or rebase leaves
    "diverged"; a re-run on the same commit is "identical"), or on any API
    error: the caller then reviews the whole PR.
    """
    auth = _pr_auth()
    if auth is None:
        return None
    (repo, _), token = auth
    url = f"{GITHUB_API_URL}/repos/{repo}/compare/{base}...{head}"
    try:
        response = httpx.get(url, headers=_headers(token), timeout=30.0)
        response.raise_for_status()
        compare = response.json()
        if compare.get("status") != "ahead":
            return None
        response = httpx.get(
            url, headers=_headers(token, "application/vnd.github.diff"), timeout=30.0
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        print(f"Warning: could not compare {base[:7]}...{head[:7]}: {exc}", file=sys.stderr)
        return None
    return response.text, int(compare.get("ahead_by") or 0)


def _headers(token: str, accept: str = "application/vnd.github+json") -> dict[str, str]:
    """Authenticated GitHub API headers; accept picks the media type (JSON, or a diff)."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def format_review_body(
    results: list[tuple[Agent, list[dict[str, Any]]]],
    failed: tuple[tuple[str, str], ...] = (),
    ignored: int = 0,
    *,
    unverified: int = 0,
    disproved: int = 0,
    reraised: int = 0,
    capped: int = 0,
    offdiff: int = 0,
    judged: int = 0,
    scope: str = "",
    fallbacks: dict[str, int] | None = None,
    agent_count: int = 0,
    unplaced: list[tuple[Agent, list[dict[str, Any]]]] | None = None,
    refused: list[tuple[Agent, list[dict[str, Any]]]] | None = None,
) -> str:
    """Generate the review summary body: counts, scope, skipped files and findings, fallbacks.

    ``unplaced`` are the ``offdiff`` findings themselves, listed so they are
    seen rather than lost; ``refused`` are findings GitHub would not take as
    inline comments, listed in the body of the review posted without them.

    ``unverified``, ``disproved``, ``reraised``, ``capped`` and ``offdiff``
    are the findings dropped for fabricated evidence, a missing-ref claim
    GitHub disproves, repeating an answered thread, exceeding
    the advisory LOW cap, and pointing outside the PR diff; see
    post_pr_review. ``fallbacks`` counts agents by the fallback model that
    served them, out of ``agent_count``, the agents that ran. ``failed``
    pairs each agent that could not finish with its failure class; they are
    listed by class, and the first line never reads as a clean review when
    one is missing. A review in which at least one agent ran and every one
    finished ends with REVIEW_MARKER; with none run, nothing was reviewed.
    One in which fewer than half finished ends with REVIEW_FAILED_MARKER.
    """
    body = _headline(results, len(failed), max(agent_count, len(failed)))
    if scope:
        body += f"\n\n{scope}"
    if ignored:
        body += f"\n\n{ignored} file(s) not reviewed, per `.gatehouse-ignore` on the base branch."
    body += _dropped(unverified, disproved, reraised, capped)
    if offdiff:
        body += f"\n\n{offdiff} finding(s) not posted inline: their line is outside the PR diff."
        body += _listed(unplaced or [])
    if refused:
        body += (
            "\n\nGitHub refused this review's inline comments, so its findings are "
            "listed here instead:"
        )
        body += _listed(refused)
    if judged:
        body += (
            f"\n\n{judged} finding(s) not raised: already judged in `{LEDGER_FILE}` on this branch."
        )
    for served, count in sorted((fallbacks or {}).items()):
        body += f"\n\n{count} of {agent_count} agents served by {served} (primary unavailable)."
    return body + _completion(failed, agent_count)


def _dropped(unverified: int, disproved: int, reraised: int, capped: int) -> str:
    """The body's lines for findings the filters dropped before posting."""
    body = ""
    if unverified:
        body += f"\n\n{unverified} finding(s) dropped: their evidence is not in the diff."
    if disproved:
        body += f"\n\n{disproved} finding(s) dropped: the ref they call missing exists on GitHub."
    if reraised:
        body += f"\n\n{reraised} finding(s) not re-raised: already answered on this PR."
    if capped:
        body += f"\n\n{capped} more low finding(s) from advisory agents not posted."
    return body


def _listed(results: list[tuple[Agent, list[dict[str, Any]]]]) -> str:
    """Findings as body lines: severity, agent, where, and what, never a reply marker.

    The marker is what triage asks an answer for, and a body line cannot be
    replied to, so it is left out.
    """
    lines = []
    for agent, findings in results:
        for finding in findings:
            severity = str(finding.get("severity", "low")).upper()
            where = _inert(strip_ansi(str(finding.get("file", "")))) or "(no file)"
            line = finding.get("lineStart", 0)
            if isinstance(line, int) and line > 0:
                where = f"{where}:{line}"
            description = _inert(strip_ansi(str(finding.get("description", ""))))
            lines.append(f"- **{severity}** ({agent.name}) `{where}`: {description}")
    return "\n\n" + "\n".join(lines) if lines else ""


def _completion(failed: tuple[tuple[str, str], ...], agent_count: int) -> str:
    """The end of the body: unfinished agents by failure class, then the marker, if any."""
    by_reason: dict[str, list[str]] = {}
    for name, reason in failed:
        by_reason.setdefault(reason, []).append(name)
    tail = "".join(
        f"\n\nIncomplete: {', '.join(names)} could not finish: {reason}."
        for reason, names in by_reason.items()
    )
    if review_failed(len(failed), agent_count):
        return f"{tail}\n\n{REVIEW_FAILED_MARKER}"
    if agent_count and not failed:
        return f"{tail}\n\n{REVIEW_MARKER}"
    return tail


def _headline(
    results: list[tuple[Agent, list[dict[str, Any]]]], failed_count: int, total: int
) -> str:
    """The count line, qualified by how many of ``total`` agents finished when some did not."""
    finished = total - failed_count
    if failed_count and not finished:
        return f"Gatehouse could not review this PR: 0 of {total} agents finished."
    line = _count_line(results)
    if failed_count:
        return f"{line.removesuffix('.')}, but only {finished} of {total} agents finished."
    return line


def _count_line(results: list[tuple[Agent, list[dict[str, Any]]]]) -> str:
    """Summarize finding counts by severity."""
    counts: dict[str, int] = {}
    total = 0
    for _, findings in results:
        for finding in findings:
            sev = finding.get("severity", "low")
            counts[sev] = counts.get(sev, 0) + 1
            total += 1

    if total == 0:
        return "Gatehouse found no issues."

    parts = []
    for sev in ("critical", "high", "medium", "low"):
        if counts.get(sev, 0) > 0:
            parts.append(f"{counts[sev]} {sev}")

    return f"Gatehouse found {total} issues ({', '.join(parts)})"


async def post_pr_review(
    results: list[tuple[Agent, list[dict[str, Any]]]],
    request_changes: bool,
    failed: tuple[tuple[str, str], ...] = (),
    ignored: int = 0,
    *,
    unverified: int = 0,
    disproved: int = 0,
    reraised: int = 0,
    capped: int = 0,
    offdiff: int = 0,
    judged: int = 0,
    unplaced: list[tuple[Agent, list[dict[str, Any]]]] | None = None,
    scope: str = "",
    fallbacks: dict[str, int] | None = None,
    agent_count: int = 0,
    commit_id: str = "",
) -> bool:
    """Post findings as a GitHub PR review via the GitHub REST API.

    When request_changes is True the review is submitted as REQUEST_CHANGES;
    otherwise (including advisory mode) it is a plain COMMENT that never gates
    the merge. ``failed`` pairs agents that could not finish with why, and ``ignored``
    counts files skipped per .gatehouse-ignore. The keyword counts are
    findings filtered out before posting: ``unverified`` quoted evidence
    not found in the diff or review context, ``disproved`` called an action
    ref missing that GitHub resolves, ``reraised`` were MEDIUM or LOW
    within a few lines of an answered thread from the same agent, and
    ``capped`` were advisory LOWs beyond the per-review limit, and
    ``offdiff`` pointed at lines GitHub cannot anchor a comment to; those
    are ``unplaced``, listed in the body instead. The review body reports
    each non-zero count, plus ``scope`` and any ``fallbacks`` (see
    format_review_body). ``commit_id`` pins the review to the commit that
    was reviewed, not whatever head is current when it posts.

    GitHub refuses a whole review (422) if any one inline comment names a
    path or line it cannot resolve in the PR. So a review with inline
    comments that GitHub refuses is posted once more with none, its findings
    listed in the body: one unplaceable finding never costs the review. Returns True on
    success, False on failure.
    """
    context = detect_pr_context()
    if context is None:
        print(
            "Warning: --comment used but not in a GitHub PR context. Skipping.",
            file=sys.stderr,
        )
        return False

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print(
            "Warning: GITHUB_TOKEN not set. Cannot post PR review.",
            file=sys.stderr,
        )
        return False

    repo, pr_number = context

    comments, inline = _inline_comments(results)

    def payload(refused: list[tuple[Agent, list[dict[str, Any]]]] | None) -> dict[str, Any]:
        body = format_review_body(
            results,
            failed,
            ignored,
            unverified=unverified,
            disproved=disproved,
            reraised=reraised,
            capped=capped,
            offdiff=offdiff,
            judged=judged,
            scope=scope,
            fallbacks=fallbacks,
            agent_count=agent_count,
            unplaced=unplaced,
            refused=refused,
        )
        sent: dict[str, Any] = {
            "event": "REQUEST_CHANGES" if request_changes else "COMMENT",
            "body": body,
        }
        if comments and refused is None:
            sent["comments"] = comments
        if commit_id:
            sent["commit_id"] = commit_id
        return sent

    if await _post_review(repo, pr_number, token, payload(None)):
        return True
    # Only a 422 is GitHub refusing the review's content. Any other failure,
    # a 5xx or a dropped connection, may have posted it, so it is not re-sent.
    if not comments or _LAST_POST.refusal != HTTPStatus.UNPROCESSABLE_ENTITY:
        return False
    print(
        "Warning: posting the review again with its inline comments in the body.",
        file=sys.stderr,
    )
    return await _post_review(repo, pr_number, token, payload(inline))


def _inline_comments(
    results: list[tuple[Agent, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], list[tuple[Agent, list[dict[str, Any]]]]]:
    """The review's inline comments, and the findings they are, by agent."""
    comments: list[dict[str, Any]] = []
    inline: list[tuple[Agent, list[dict[str, Any]]]] = []
    for agent, findings in results:
        placed = []
        for finding in findings:
            file_path = finding.get("file", "")
            line = finding.get("lineStart", 0)
            if not file_path or line <= 0:
                continue
            placed.append(finding)
            comments.append(
                {
                    "path": file_path,
                    "line": line,
                    "body": _format_comment_body(agent, finding),
                }
            )
        if placed:
            inline.append((agent, placed))
    return comments, inline


@dataclass
class _LastPost:
    """The HTTP status GitHub refused the last review post with, or None when
    it succeeded or got no answer. A side channel rather than a return value:
    sdlc records reviews by replacing _post_review, so its signature cannot
    change (sdlc_jobs/_gatehouse_shim.py)."""

    refusal: int | None = None


_LAST_POST = _LastPost()


async def _post_review(repo: str, pr_number: int, token: str, payload: dict[str, Any]) -> bool:
    """POST one PR review. Returns True on success, False after warning why not."""
    _LAST_POST.refusal = None
    url = f"{GITHUB_API_URL}/repos/{repo}/pulls/{pr_number}/reviews"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=_headers(token), json=payload, timeout=30.0)
        if response.status_code >= 400:
            _LAST_POST.refusal = response.status_code
            print(
                f"Warning: GitHub API returned {response.status_code}: {response.text[:200]}",
                file=sys.stderr,
            )
            return False
    except httpx.HTTPError as exc:
        print(f"Warning: Failed to post PR review: {exc}", file=sys.stderr)
        return False
    return True


async def post_empty_review(note: str, commit_id: str = "") -> bool:
    """Post a clean review of a diff with nothing to send, ending with REVIEW_MARKER.

    For a PR, or the commits since the last review, that touch only files
    `.gatehouse-ignore` skips: triage reads the review of the head, so
    without one it would hold forever. ``note`` must be fixed text, never
    PR-controlled: this body carries the complete marker.
    Returns True on success, False on failure.
    """
    context = detect_pr_context()
    token = os.environ.get("GITHUB_TOKEN", "")
    if context is None or not token:
        print(
            "Warning: cannot post the review: no PR context or GITHUB_TOKEN.",
            file=sys.stderr,
        )
        return False
    payload: dict[str, Any] = {
        "event": "COMMENT",
        "body": f"Gatehouse found no issues.\n\n{_inert(note)}\n\n{REVIEW_MARKER}",
    }
    if commit_id:
        payload["commit_id"] = commit_id
    return await _post_review(*context, token, payload)


async def post_failed_review(reason: str, commit_id: str = "") -> bool:
    """Post a review saying why none ran, ending with REVIEW_FAILED_MARKER.

    For a refusal before any agent is called (an oversize diff, a bad
    limit): without it, triage would pass on the previous review, or on none.
    Returns True on success, False on failure.
    """
    context = detect_pr_context()
    token = os.environ.get("GITHUB_TOKEN", "")
    if context is None or not token:
        print(
            "Warning: cannot post the failed review: no PR context or GITHUB_TOKEN.",
            file=sys.stderr,
        )
        return False
    payload: dict[str, Any] = {
        "event": "COMMENT",
        "body": f"Gatehouse could not review this PR: {_inert(reason)}\n\n{REVIEW_FAILED_MARKER}",
    }
    if commit_id:
        payload["commit_id"] = commit_id
    return await _post_review(*context, token, payload)
