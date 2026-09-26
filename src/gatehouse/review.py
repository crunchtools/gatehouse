"""Core review orchestration."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from gatehouse.agents import (
    CONSTITUTION,
    Agent,
    build_constitution_prompt,
    build_user_prompt,
    get_agents,
)
from gatehouse.github import fetch_repo_file, post_pr_review
from gatehouse.ignore import IGNORE_FILE, filter_diff, filter_listing, parse_ignore
from gatehouse.llm import DEFAULT_MODEL, call_model
from gatehouse.output import format_results, print_summary

CONFIDENCE_THRESHOLD = 80

BLOCKING_SEVERITIES = frozenset({"critical", "high"})

MAX_CONCURRENT_AGENTS = 5


def detect_default_branch() -> str:
    """Auto-detect the default branch (works in worktrees too)."""
    origin_head = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        return ref.removeprefix("refs/remotes/origin/")

    for candidate in ("main", "master"):
        check = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode == 0:
            return candidate

    return "main"


def get_git_diff(base: str | None, staged: bool) -> str:
    """Get the git diff for review."""
    if staged:
        cmd = ["git", "diff", "--staged"]
    else:
        resolved_base = base if base is not None else detect_default_branch()
        cmd = ["git", "diff", f"{resolved_base}...HEAD"]
    diff_proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if diff_proc.returncode != 0:
        print(
            f"Error running git diff: {diff_proc.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(2)
    return diff_proc.stdout


def get_file_listing() -> str | None:
    """Get git-tracked file listing for context."""
    ls_proc = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=False)
    if ls_proc.returncode != 0:
        return None
    return ls_proc.stdout


STYLEGUIDE_PATH = ".gemini/styleguide.md"

CONSTITUTION_SEARCH_PATHS: tuple[str, ...] = (
    ".specify/memory/constitution.md",
    "AGENTS.md",
    "CLAUDE.md",
)


def _context_source() -> tuple[str, str] | None:
    """Return (repo, ref) for loading trusted context files via the GitHub API.

    Set GATEHOUSE_CONTEXT_REPO and GATEHOUSE_CONTEXT_REF (the base repo and
    base-branch SHA of a pull request) to load the styleguide/constitution from
    the trusted base over the API — no checkout required. Used by the fork-safe
    reusable workflow. The ref must be the base, never the PR head.
    """
    repo = os.environ.get("GATEHOUSE_CONTEXT_REPO")
    ref = os.environ.get("GATEHOUSE_CONTEXT_REF")
    if repo and ref:
        return repo, ref
    return None


def _load_context_file(relpath: str) -> str | None:
    """Load a context file from the trusted base via API, else local disk.

    Under GATEHOUSE_CONTEXT_REPO/REF only the base copy counts, even when the
    base has none: a PR cannot supply the rules it is reviewed by (for the
    ignore file, that would hide its own changes).
    """
    source = _context_source()
    if source is not None:
        token = os.environ.get("GITHUB_TOKEN", "")
        return fetch_repo_file(source[0], source[1], relpath, token)

    path = Path(relpath)
    if path.exists():
        return path.read_text()
    return None


def load_styleguide() -> str | None:
    """Load the styleguide: trusted base only when one is set, else local disk."""
    return _load_context_file(STYLEGUIDE_PATH)


def load_constitution(override_path: str | None = None) -> str | None:
    """Load a project constitution file.

    Search order: explicit override, then — when GATEHOUSE_CONTEXT_REPO/REF are
    set — the trusted base repo via the API and nothing else, otherwise local
    disk (.specify/, AGENTS.md, CLAUDE.md).
    """
    if override_path is not None:
        path = Path(override_path)
        if not path.exists():
            print(
                f"Error: constitution file not found: {override_path}",
                file=sys.stderr,
            )
            sys.exit(2)
        return path.read_text()

    source = _context_source()
    if source is not None:
        token = os.environ.get("GITHUB_TOKEN", "")
        for candidate in CONSTITUTION_SEARCH_PATHS:
            content = fetch_repo_file(source[0], source[1], candidate, token)
            if content is not None:
                return content
        return None

    for candidate in CONSTITUTION_SEARCH_PATHS:
        path = Path(candidate)
        if path.exists():
            return path.read_text()
    return None


def _has_blocking_findings(
    results: list[tuple[Agent, list[dict[str, Any]]]],
) -> bool:
    """Check if any blocking agent has critical/high findings."""
    for agent, findings in results:
        if agent.blocking:
            for finding in findings:
                if finding.get("severity", "low") in BLOCKING_SEVERITIES:
                    return True
    return False


def _parse_findings(response_text: str) -> list[dict[str, Any]]:
    """Return the confident findings in a model reply.

    Raises ValueError (JSONDecodeError included) or TypeError when the reply
    is not a list of finding objects: that is an unfinished review, not a
    clean one.
    """
    findings_raw = json.loads(response_text)
    if isinstance(findings_raw, dict):
        # Some models wrap the array: {"findings": [...]}.
        findings_raw = findings_raw.get("findings")
    if not isinstance(findings_raw, list) or not all(isinstance(f, dict) for f in findings_raw):
        raise TypeError(f"reply is {type(findings_raw).__name__}, not a list of findings")
    return [f for f in findings_raw if f.get("confidence", 0) >= CONFIDENCE_THRESHOLD]


async def run_agent(
    client: httpx.AsyncClient,
    agent: Agent,
    user_prompt: str,
    model: str,
    api_key: str,
    verbose: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[Agent, list[dict[str, Any]] | None]:
    """Run a single agent and return its findings, or None if it failed."""
    async with semaphore:
        try:
            response_text = await call_model(
                client, agent.system_prompt, user_prompt, model, api_key
            )
            if verbose:
                print(f"\n--- {agent.name} raw response ---", file=sys.stderr)
                print(response_text, file=sys.stderr)
            findings: list[dict[str, Any]] | None = _parse_findings(response_text)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            print(f"Error: {agent.name} did not finish: {exc!r}", file=sys.stderr)
            findings = None
    return agent, findings


async def run_review(
    base: str | None = None,
    staged: bool = False,
    stdin_diff: str | None = None,
    agent_slugs: list[str] | None = None,
    model: str = DEFAULT_MODEL,
    advisory: bool = False,
    verbose: bool = False,
    api_key: str = "",
    constitution_path: str | None = None,
    comment: bool = False,
) -> int:
    """Run the full review pipeline.

    When comment=True, posts findings as a GitHub PR review after printing.
    Returns exit code (0 clean, 1 blocking, 2 usage error or an agent that
    could not finish). An unfinished agent outranks findings, except under
    --advisory, which never fails the run: there it is reported, not raised.
    """
    diff = stdin_diff if stdin_diff is not None else get_git_diff(base, staged)
    spec = parse_ignore(_load_context_file(IGNORE_FILE)) if diff.strip() else None
    diff, ignored = filter_diff(diff, spec)
    if ignored:
        print(f"Ignored {len(ignored)} file(s) per {IGNORE_FILE}.", file=sys.stderr)
    if not diff.strip():
        print("No changes to review.")
        return 0

    agents = get_agents(agent_slugs)
    styleguide = load_styleguide()
    file_listing = filter_listing(get_file_listing(), spec)
    user_prompt = build_user_prompt(diff, styleguide, file_listing)

    constitution = load_constitution(constitution_path)
    constitution_agent = None
    standard_agents = []
    for agent in agents:
        if agent.slug == CONSTITUTION.slug:
            constitution_agent = agent
        else:
            standard_agents.append(agent)

    if constitution_agent and not constitution:
        print("Skipping Constitution agent: no constitution file found.")
        constitution_agent = None

    async with httpx.AsyncClient() as client:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
        coros = [
            run_agent(
                client,
                agent,
                user_prompt,
                model,
                api_key,
                verbose,
                semaphore,
            )
            for agent in standard_agents
        ]
        if constitution_agent and constitution:
            const_prompt = build_constitution_prompt(
                diff,
                constitution,
                styleguide,
                file_listing,
            )
            coros.append(
                run_agent(
                    client,
                    constitution_agent,
                    const_prompt,
                    model,
                    api_key,
                    verbose,
                    semaphore,
                )
            )
        raw = await asyncio.gather(*coros)

    failed = tuple(agent.name for agent, findings in raw if findings is None)
    all_results: list[tuple[Agent, list[dict[str, Any]]]] = [
        (agent, findings) for agent, findings in raw if findings is not None
    ]

    format_results(all_results)

    has_blocking = _has_blocking_findings(all_results)
    request_changes = has_blocking and not advisory
    exit_code = 1 if request_changes else 0
    if failed:
        print(f"Review incomplete: {', '.join(failed)} could not finish.", file=sys.stderr)
        if not advisory:
            exit_code = 2
    print_summary(all_results, exit_code)

    if comment:
        await post_pr_review(all_results, request_changes, failed, len(ignored))

    return exit_code
