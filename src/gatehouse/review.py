"""Core review orchestration."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from gatehouse.agents import Agent, build_user_prompt, get_agents
from gatehouse.gemini import DEFAULT_MODEL, call_gemini
from gatehouse.output import format_results, print_summary

CONFIDENCE_THRESHOLD = 80

BLOCKING_SEVERITIES = frozenset({"critical", "high"})


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
    ls_proc = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=False
    )
    if ls_proc.returncode != 0:
        return None
    return ls_proc.stdout


def load_styleguide() -> str | None:
    """Load .gemini/styleguide.md if it exists in the current directory."""
    path = Path(".gemini/styleguide.md")
    if path.exists():
        return path.read_text()
    return None


async def run_agent(
    client: httpx.AsyncClient,
    agent: Agent,
    user_prompt: str,
    model: str,
    api_key: str,
    verbose: bool,
) -> tuple[Agent, list[dict[str, Any]]]:
    """Run a single agent and return its findings."""
    try:
        response_text = await call_gemini(
            client, agent.system_prompt, user_prompt, model, api_key
        )
        if verbose:
            print(f"\n--- {agent.name} raw response ---", file=sys.stderr)
            print(response_text, file=sys.stderr)
        findings_raw = json.loads(response_text)
        if not isinstance(findings_raw, list):
            findings_raw = []
        findings: list[dict[str, Any]] = [
            f
            for f in findings_raw
            if isinstance(f, dict)
            and f.get("confidence", 0) >= CONFIDENCE_THRESHOLD
        ]
    except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as exc:
        print(f"Warning: {agent.name} failed: {exc}", file=sys.stderr)
        findings = []
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
) -> int:
    """Run the full review pipeline. Returns exit code."""
    diff = stdin_diff if stdin_diff is not None else get_git_diff(base, staged)
    if not diff.strip():
        print("No changes to review.")
        return 0

    agents = get_agents(agent_slugs)
    styleguide = load_styleguide()
    file_listing = get_file_listing()
    user_prompt = build_user_prompt(diff, styleguide, file_listing)

    async with httpx.AsyncClient() as client:
        coros = [
            run_agent(client, agent, user_prompt, model, api_key, verbose)
            for agent in agents
        ]
        raw = await asyncio.gather(*coros)

    all_results: list[tuple[Agent, list[dict[str, Any]]]] = list(raw)

    format_results(all_results)

    has_blocking = False
    for agent, findings in all_results:
        if agent.blocking:
            for finding in findings:
                severity = finding.get("severity", "low")
                if severity in BLOCKING_SEVERITIES:
                    has_blocking = True

    exit_code = 1 if has_blocking and not advisory else 0
    print_summary(all_results, exit_code)
    return exit_code
