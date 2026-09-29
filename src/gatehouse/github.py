"""GitHub PR review integration via httpx."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import TYPE_CHECKING, Any

import httpx

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


# The first line of every finding comment; triage.yml parses the same prefix.
FINDING_HEADER_RE = re.compile(r"^\*\*(CRITICAL|HIGH|MEDIUM|LOW)\*\* \(([^)]+)\):")


def _format_comment_body(agent: Agent, finding: dict[str, Any]) -> str:
    """Format a single finding as a PR review comment body.

    ANSI escape sequences are stripped from the finding's description,
    suggestion, and evidence before they are embedded in the comment. A
    hidden marker records the agent slug and confidence, so replies can be
    joined back to confidence when tuning the threshold.
    """
    severity = finding.get("severity", "low").upper()
    description = strip_ansi(finding.get("description", ""))
    suggestion = strip_ansi(finding.get("suggestion", ""))
    evidence = strip_ansi(finding.get("evidence", ""))

    parts = [f"**{severity}** ({agent.name}): {description}"]
    if suggestion:
        parts.append(f"\n**Suggestion:** {suggestion}")
    if evidence:
        parts.append(f"\n**Evidence:**\n```\n{evidence}\n```")
    confidence = int(finding.get("confidence", 0))
    parts.append(f"\n<!-- gatehouse agent={agent.slug} confidence={confidence} -->")
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
    REVIEW_MARKER; requiring a bot keeps a PR participant from planting a
    base that would skip their commits. Outside a PR context, with no such
    review, or on any API error, returns None (with a warning on error):
    the caller then reviews the whole PR.
    """
    commits = [
        r["commit_id"]
        for r in _fetch_pr_list("reviews", "earlier reviews")
        if (r.get("user") or {}).get("type") == "Bot"
        and REVIEW_MARKER in (r.get("body") or "")
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
    failed: tuple[str, ...] = (),
    ignored: int = 0,
    *,
    unverified: int = 0,
    reraised: int = 0,
    capped: int = 0,
    offdiff: int = 0,
    scope: str = "",
    fallbacks: dict[str, int] | None = None,
    agent_count: int = 0,
) -> str:
    """Generate the review summary body: counts, scope, skipped files and findings, fallbacks.

    ``unverified``, ``reraised``, ``capped`` and ``offdiff`` are the findings
    dropped for fabricated evidence, repeating an answered thread, exceeding
    the advisory LOW cap, and pointing outside the PR diff; see
    post_pr_review. ``fallbacks`` counts agents by the fallback model that
    served them, out of ``agent_count``, the agents that ran. A review in
    which at least one agent ran and every one finished ends with
    REVIEW_MARKER; with none run, nothing was reviewed.
    """
    body = _count_line(results)
    if scope:
        body += f"\n\n{scope}"
    if ignored:
        body += f"\n\n{ignored} file(s) not reviewed, per `.gatehouse-ignore` on the base branch."
    if unverified:
        body += f"\n\n{unverified} finding(s) dropped: their evidence is not in the diff."
    if reraised:
        body += f"\n\n{reraised} finding(s) not re-raised: already answered on this PR."
    if capped:
        body += f"\n\n{capped} more low finding(s) from advisory agents not posted."
    if offdiff:
        body += f"\n\n{offdiff} finding(s) not posted: their line is outside the PR diff."
    for served, count in sorted((fallbacks or {}).items()):
        body += f"\n\n{count} of {agent_count} agents served by {served} (primary unavailable)."
    if failed:
        body += f"\n\nIncomplete: {', '.join(failed)} could not finish."
    elif agent_count:
        body += f"\n\n{REVIEW_MARKER}"
    return body


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
    failed: tuple[str, ...] = (),
    ignored: int = 0,
    *,
    unverified: int = 0,
    reraised: int = 0,
    capped: int = 0,
    offdiff: int = 0,
    scope: str = "",
    fallbacks: dict[str, int] | None = None,
    agent_count: int = 0,
    commit_id: str = "",
) -> bool:
    """Post findings as a GitHub PR review via the GitHub REST API.

    When request_changes is True the review is submitted as REQUEST_CHANGES;
    otherwise (including advisory mode) it is a plain COMMENT that never gates
    the merge. ``failed`` names agents that could not finish and ``ignored``
    counts files skipped per .gatehouse-ignore. The keyword counts are
    findings filtered out before posting: ``unverified`` quoted evidence
    not found in the diff or review context, ``reraised`` were MEDIUM or LOW
    within a few lines of an answered thread from the same agent, and
    ``capped`` were advisory LOWs beyond the per-review limit, and
    ``offdiff`` pointed at lines GitHub cannot anchor a comment to. The
    review body reports each non-zero count, plus ``scope`` and any
    ``fallbacks`` (see format_review_body). ``commit_id`` pins the review to
    the commit that was reviewed, not whatever head is current when it
    posts. Returns True on success, False on failure.
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

    comments: list[dict[str, Any]] = []
    for agent, findings in results:
        for finding in findings:
            file_path = finding.get("file", "")
            line = finding.get("lineStart", 0)
            if not file_path or line <= 0:
                continue
            comments.append(
                {
                    "path": file_path,
                    "line": line,
                    "body": _format_comment_body(agent, finding),
                }
            )

    body = format_review_body(
        results,
        failed,
        ignored,
        unverified=unverified,
        reraised=reraised,
        capped=capped,
        offdiff=offdiff,
        scope=scope,
        fallbacks=fallbacks,
        agent_count=agent_count,
    )
    event = "REQUEST_CHANGES" if request_changes else "COMMENT"

    payload: dict[str, Any] = {
        "event": event,
        "body": body,
    }
    if comments:
        payload["comments"] = comments
    if commit_id:
        payload["commit_id"] = commit_id

    url = f"{GITHUB_API_URL}/repos/{repo}/pulls/{pr_number}/reviews"
    headers = _headers(token)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=30.0,
            )
        if response.status_code >= 400:
            print(
                f"Warning: GitHub API returned {response.status_code}: {response.text[:200]}",
                file=sys.stderr,
            )
            return False
    except httpx.HTTPError as exc:
        print(
            f"Warning: Failed to post PR review: {exc}",
            file=sys.stderr,
        )
        return False

    return True
