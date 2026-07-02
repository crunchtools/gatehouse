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

    Returns (repo, pr_number) or None if not in a PR context.
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
    a prompt-injecting styleguide. Returns the file text, or None if absent/error.
    """
    url = f"{GITHUB_API_URL}/repos/{repo}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = httpx.get(
            url, headers=headers, params={"ref": ref}, timeout=15.0
        )
    except httpx.HTTPError as exc:
        print(
            f"Warning: could not fetch {path} from {repo}@{ref}: {exc}",
            file=sys.stderr,
        )
        return None
    if response.is_success:
        return response.text
    return None


def _format_comment_body(
    agent_name: str, finding: dict[str, Any]
) -> str:
    """Format a single finding as a PR review comment body."""
    severity = finding.get("severity", "low").upper()
    description = strip_ansi(finding.get("description", ""))
    suggestion = strip_ansi(finding.get("suggestion", ""))
    evidence = strip_ansi(finding.get("evidence", ""))

    parts = [f"**{severity}** ({agent_name}): {description}"]
    if suggestion:
        parts.append(f"\n**Suggestion:** {suggestion}")
    if evidence:
        parts.append(f"\n**Evidence:**\n```\n{evidence}\n```")
    return "\n".join(parts)


def format_review_body(
    results: list[tuple[Agent, list[dict[str, Any]]]],
) -> str:
    """Generate the review summary body line."""
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
    has_blocking: bool,
) -> bool:
    """Post findings as a GitHub PR review via the GitHub REST API.

    Returns True on success, False on failure.
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
            comments.append({
                "path": file_path,
                "line": line,
                "body": _format_comment_body(agent.name, finding),
            })

    body = format_review_body(results)
    event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

    payload: dict[str, Any] = {
        "event": event,
        "body": body,
    }
    if comments:
        payload["comments"] = comments

    url = f"{GITHUB_API_URL}/repos/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

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
                f"Warning: GitHub API returned {response.status_code}: "
                f"{response.text[:200]}",
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
