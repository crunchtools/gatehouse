"""Core review orchestration."""

from __future__ import annotations

import asyncio
import http
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from gatehouse.agents import (
    CONSTITUTION,
    Agent,
    build_constitution_prompt,
    build_user_prompt,
    get_agents,
)
from gatehouse.diffview import right_side_lines
from gatehouse.github import (
    detect_pr_context,
    fetch_answered_threads,
    fetch_compare,
    fetch_last_reviewed_commit,
    fetch_repo_file,
    post_pr_review,
    review_failed,
)
from gatehouse.ignore import (
    IGNORE_FILE,
    diff_paths,
    filter_diff,
    filter_listing,
    parse_ignore,
    restrict_diff,
)
from gatehouse.llm import DEFAULT_MODEL, Completion, call_model
from gatehouse.output import format_results, print_summary, strip_ansi

if TYPE_CHECKING:
    from collections.abc import Callable

CONFIDENCE_THRESHOLD = 80

BLOCKING_SEVERITIES = frozenset({"critical", "high"})

# LOWs from these agents (by name, as printed in each finding) are required
# answers, matching triage.yml's `required_low_agents` default; every other
# agent's LOWs are advisory, and only the most confident few are posted.
REQUIRED_LOW_AGENTS = frozenset({"Bug Hunter", "Security Scan"})
MAX_ADVISORY_LOWS = 5

# A new finding this close to an answered thread by the same agent is a
# re-raise. HIGH/CRITICAL are always posted: next to a "fixed in" thread
# they may be a regression.
RERAISE_WINDOW = 5
RERAISE_SEVERITIES = frozenset({"medium", "low"})

_EVIDENCE_LINE_NO_RE = re.compile(r"^\s*(?:line\s+)?\d+\s*(?:\[[+-]\])?\s*[:|]\s?", re.I)
_EVIDENCE_MIN_CHARS = 4

Results = list[tuple[Agent, list[dict[str, Any]]]]
# One agent's run: its findings (None if it failed), the model's reply, and
# the failure class when it failed.
AgentRun = tuple[Agent, list[dict[str, Any]] | None, Completion | None, str | None]

MAX_CONCURRENT_AGENTS = 5

# What a provider status means to whoever has to fix it.
_STATUS_LABELS: dict[int, str] = {
    http.HTTPStatus.UNAUTHORIZED: "key rejected",
    http.HTTPStatus.PAYMENT_REQUIRED: "payment required",
    http.HTTPStatus.FORBIDDEN: "forbidden",
    http.HTTPStatus.TOO_MANY_REQUESTS: "rate limited",
}


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
    findings_raw: list[Any] = []
    for value in _json_values(response_text):
        # Some models wrap the array: {"findings": [...]}.
        found = value.get("findings") if isinstance(value, dict) else value
        if not isinstance(found, list) or not all(isinstance(f, dict) for f in found):
            raise TypeError(f"reply is {type(found).__name__}, not a list of findings")
        findings_raw.extend(found)
    return [f for f in findings_raw if f.get("confidence", 0) >= CONFIDENCE_THRESHOLD]


def _json_values(text: str) -> list[Any]:
    """Every JSON value at the start of ``text``, back to back.

    Some models (GLM-5.3) emit a second array, or prose, after the first
    array. Consecutive JSON values are all kept so no findings are dropped;
    trailing non-JSON text is ignored. Raises JSONDecodeError when the text
    does not start with JSON at all.
    """
    decoder = json.JSONDecoder()
    values: list[Any] = []
    pos = len(text) - len(text.lstrip())
    while pos < len(text):
        try:
            value, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            if not values:
                raise
            break
        values.append(value)
        pos += len(text[pos:]) - len(text[pos:].lstrip())
    if not values:
        raise json.JSONDecodeError("Expecting value", text, pos)
    return values


def _squash(text: str) -> str:
    return "".join(strip_ansi(text).split())


def _evidence_haystack(diff: str, *context: str | None) -> str:
    """Everything an agent was shown, whitespace removed, diff markers dropped.

    Lines are joined without a separator so a quote reflowed across lines
    still matches.
    """
    parts = [_squash(line[1:]) for line in diff.splitlines()]
    parts.extend(_squash(c) for c in context if c)
    return "".join(parts)


def _evidence_is_real(evidence: str, haystack: str) -> bool:
    """True unless a quoted piece of the evidence appears nowhere agents looked.

    Line-number prefixes (``Line 10:``, the diff view's ``  10 [+]|``) and
    +/- markers are stripped, ``...`` elisions split a line into pieces, and
    whitespace is ignored, so reflowed or trimmed quotes still match. Evidence
    with nothing checkable in it is given the benefit of the doubt.
    """
    for raw in strip_ansi(evidence).splitlines():
        if raw.lstrip().startswith("```"):
            continue
        code = _EVIDENCE_LINE_NO_RE.sub("", raw).lstrip()
        if code[:1] in ("+", "-"):
            code = code[1:]
        for piece in re.split(r"\.\.\.|…", code):
            squashed = _squash(piece)
            if len(squashed) >= _EVIDENCE_MIN_CHARS and squashed not in haystack:
                return False
    return True


def _keep_where(
    results: Results, keep: Callable[[Agent, dict[str, Any]], bool]
) -> tuple[Results, int]:
    """Keep the findings keep() accepts; return them and how many were dropped."""
    kept: Results = [
        (agent, [f for f in findings if keep(agent, f)]) for agent, findings in results
    ]
    dropped = sum(len(f) for _, f in results) - sum(len(f) for _, f in kept)
    return kept, dropped


def _drop_unverified(results: Results, haystack: str) -> tuple[Results, int]:
    return _keep_where(
        results, lambda _, f: _evidence_is_real(str(f.get("evidence", "")), haystack)
    )


def _drop_reraised(results: Results, threads: list[tuple[str, str, int]]) -> tuple[Results, int]:
    answered_lines: dict[tuple[str, str], set[int]] = {}
    for name, path, t_line in threads:
        answered_lines.setdefault((name, path), set()).add(t_line)

    def reraises(agent: Agent, finding: dict[str, Any]) -> bool:
        if finding.get("severity", "low") not in RERAISE_SEVERITIES:
            return False
        lines = answered_lines.get((agent.name, finding.get("file", "")))
        if not lines:
            return False
        line = finding.get("lineStart", 0)
        return any(n in lines for n in range(line - RERAISE_WINDOW, line + RERAISE_WINDOW + 1))

    return _keep_where(results, lambda agent, f: not reraises(agent, f))


def _cap_advisory_lows(
    results: Results, required_low: frozenset[str] = REQUIRED_LOW_AGENTS
) -> tuple[Results, int]:
    """Keep the MAX_ADVISORY_LOWS most confident LOWs from agents not in required_low."""
    advisory = [
        f
        for agent, findings in results
        if agent.name not in required_low
        for f in findings
        if f.get("severity", "low") == "low"
    ]
    advisory.sort(key=lambda f: f.get("confidence", 0), reverse=True)
    cut = {id(f) for f in advisory[MAX_ADVISORY_LOWS:]}
    kept: Results = [
        (agent, [f for f in findings if id(f) not in cut]) for agent, findings in results
    ]
    return kept, len(cut)


def _drop_offdiff(results: Results, anchors: dict[str, set[int]] | None) -> tuple[Results, int]:
    """Drop findings whose file line is not shown in the PR diff.

    GitHub rejects a whole review if one comment points outside the diff.
    Findings with no file or line are kept: they are never posted inline.
    With no anchors (not commenting), nothing is dropped.
    """
    if anchors is None:
        return results, 0

    def anchored(_: Agent, finding: dict[str, Any]) -> bool:
        path, line = finding.get("file", ""), finding.get("lineStart", 0)
        if not path or not isinstance(line, int) or line <= 0:
            return True
        return line in anchors.get(path, ())

    return _keep_where(results, anchored)


def _filter_findings(
    results: Results,
    haystack: str,
    answered: list[tuple[str, str, int]],
    required_low: frozenset[str] = REQUIRED_LOW_AGENTS,
    anchors: dict[str, set[int]] | None = None,
) -> tuple[Results, dict[str, int]]:
    """Drop unverifiable evidence, off-diff lines, re-raises of answered threads, excess LOWs.

    Returns the kept results and the count dropped by each filter, keyed as
    post_pr_review takes them.
    """
    results, unverified = _drop_unverified(results, haystack)
    results, offdiff = _drop_offdiff(results, anchors)
    results, reraised = _drop_reraised(results, answered)
    results, capped = _cap_advisory_lows(results, required_low)
    if unverified:
        print(f"Dropped {unverified} finding(s): evidence not in the diff.", file=sys.stderr)
    if offdiff:
        print(f"Dropped {offdiff} finding(s): line outside the PR diff.", file=sys.stderr)
    if reraised:
        print(f"Skipped {reraised} finding(s) already answered on this PR.", file=sys.stderr)
    if capped:
        print(
            f"Held back {capped} low finding(s) from advisory agents "
            f"(at most {MAX_ADVISORY_LOWS} are shown).",
            file=sys.stderr,
        )
    return results, {
        "unverified": unverified,
        "offdiff": offdiff,
        "reraised": reraised,
        "capped": capped,
    }


def _failure_class(exc: Exception) -> str:
    """Name the kind of failure for the posted review: a class, never the provider's own text."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == http.HTTPStatus.OK:  # call_model: a 200 that carried no review
            return "no review in the reply"
        label = _STATUS_LABELS.get(status)
        return f"HTTP {status} ({label})" if label else f"HTTP {status}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "connection error"
    return "unparseable reply"


async def run_agent(
    client: httpx.AsyncClient,
    agent: Agent,
    *,
    user_prompt: str,
    model: str,
    api_key: str,
    verbose: bool,
    semaphore: asyncio.Semaphore,
) -> AgentRun:
    """Run a single agent; return its findings, the model's reply, and why it failed.

    Findings are None, and the reason a failure class (see _failure_class),
    only when the agent did not finish. Logs the serving model and token
    usage of every reply, even one whose findings do not parse: the tokens
    were spent either way.
    """
    reply: Completion | None = None
    reason: str | None = None
    async with semaphore:
        try:
            reply = await call_model(client, agent.system_prompt, user_prompt, model, api_key)
            print(_usage_line(agent.slug, reply), file=sys.stderr)
            if verbose:
                print(f"\n--- {agent.name} raw response ---", file=sys.stderr)
                print(reply.text, file=sys.stderr)
            findings: list[dict[str, Any]] | None = _parse_findings(reply.text)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            print(f"Error: {agent.name} did not finish: {exc!r}", file=sys.stderr)
            findings = None
            reason = _failure_class(exc)
    return agent, findings, reply, reason


def _usage_record(slug: str, reply: Completion) -> dict[str, Any]:
    """One agent's serving model, token counts, cost and fallback flag."""
    prompt, completion, reasoning, cost = reply.tokens()
    return {
        "agent": slug,
        "model": reply.model or reply.requested,
        "prompt": prompt,
        "completion": completion,
        "reasoning": reasoning,
        "cost": cost,
        "fallback": reply.fallback,
    }


def _usage_line(slug: str, reply: Completion) -> str:
    """The stderr log line for one agent's call."""
    r = _usage_record(slug, reply)
    return (
        f"agent={r['agent']} model={r['model']} prompt={r['prompt']} "
        f"completion={r['completion']} reasoning={r['reasoning']} cost=${r['cost']:.4f} "
        f"fallback={'yes' if r['fallback'] else 'no'}"
    )


def _usage_total(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Calls, summed tokens and cost, and fallback count across usage records."""
    return {
        "calls": len(records),
        "prompt": sum(r["prompt"] for r in records),
        "completion": sum(r["completion"] for r in records),
        "reasoning": sum(r["reasoning"] for r in records),
        "cost": sum(r["cost"] for r in records),
        "fallback": sum(1 for r in records if r["fallback"]),
    }


def _write_step_summary(path: str, records: list[dict[str, Any]], total: dict[str, Any]) -> None:
    """Append a per-agent usage table, with a total row, to the GHA step summary."""
    rows = [
        "| Agent | Model | Prompt | Completion | Reasoning | Cost | Fallback |",
        "|---|---|---:|---:|---:|---:|---|",
        *(
            f"| {r['agent']} | {r['model']} | {r['prompt']} | {r['completion']} | "
            f"{r['reasoning']} | ${r['cost']:.4f} | {'yes' if r['fallback'] else 'no'} |"
            for r in records
        ),
        (
            f"| **total** | | {total['prompt']} | {total['completion']} | {total['reasoning']} | "
            f"${total['cost']:.4f} | {total['fallback']}/{total['calls']} |"
        ),
    ]
    try:
        with open(path, "a") as f:
            f.write("### Gatehouse usage\n\n" + "\n".join(rows) + "\n")
    except OSError as e:
        print(f"Warning: could not write step summary {path}: {e}", file=sys.stderr)


def _report_usage(records: list[dict[str, Any]]) -> dict[str, int]:
    """Print the review's usage total; return the count of agents per fallback model."""
    total = _usage_total(records)
    print(
        f"usage: calls={total['calls']} prompt={total['prompt']} "
        f"completion={total['completion']} reasoning={total['reasoning']} "
        f"cost=${total['cost']:.4f} fallback={total['fallback']}/{total['calls']}",
        file=sys.stderr,
    )
    fallbacks: dict[str, int] = {}
    for r in records:
        if r["fallback"]:
            fallbacks[r["model"]] = fallbacks.get(r["model"], 0) + 1
    return fallbacks


def _save_usage(records: list[dict[str, Any]], usage_json: str | None) -> None:
    """Write usage to the GHA step summary (when set) and to usage_json.

    Runs after the review is posted, so a bad usage_json path costs the exit
    status, never the findings. The step summary is cosmetic: a failed write
    only warns.
    """
    total = _usage_total(records)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and records:
        _write_step_summary(summary, records, total)
    if usage_json:
        Path(usage_json).write_text(json.dumps({"agents": records, "total": total}, indent=2))


def _incremental_diff(pr_diff: str) -> tuple[str, str] | None:
    """Return (diff, scope line) covering only commits since the last complete review.

    None — review the whole PR — when not in a PR with a known head, when
    there is no earlier complete review, or when head does not descend from
    it. The compare diff is cut to files the PR touches, so merging the base
    branch in does not put the base's own changes up for review.
    """
    head = os.environ.get("GATEHOUSE_HEAD_SHA", "")
    if not head or detect_pr_context() is None:
        print("Full review: no PR head to compare against.", file=sys.stderr)
        return None
    base = fetch_last_reviewed_commit()
    if base is None:
        print("Full review: no earlier complete Gatehouse review.", file=sys.stderr)
        return None
    compared = fetch_compare(base, head)
    if compared is None:
        print(
            f"Full review: {head[:7]} does not build on reviewed {base[:7]}.",
            file=sys.stderr,
        )
        return None
    diff, commits = compared
    diff = restrict_diff(diff, diff_paths(pr_diff))
    noun = "commit" if commits == 1 else "commits"
    scope = f"Reviewed {commits} {noun} since {base[:7]} (incremental)."
    print(scope, file=sys.stderr)
    return diff, scope


async def _review_scope(pr_diff: str, spec: Any, *, incremental: bool) -> tuple[str, str]:
    """Return the diff to review and the scope line for the posted review body.

    The whole PR unless incremental review applies and finds a range; the
    range is filtered by the same ignore spec as the PR diff. The GitHub
    lookups block, so they run in a thread.
    """
    if incremental:
        since = await asyncio.to_thread(_incremental_diff, pr_diff)
        if since is not None:
            return filter_diff(since[0], spec)[0], since[1]
    return pr_diff, "Full review."


async def _run_agents(
    prompts: list[tuple[Agent, str]], *, model: str, api_key: str, verbose: bool
) -> list[AgentRun]:
    """Run each agent on its prompt concurrently, at most MAX_CONCURRENT_AGENTS at once."""
    async with httpx.AsyncClient() as client:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
        return await asyncio.gather(
            *(
                run_agent(
                    client,
                    agent,
                    user_prompt=prompt,
                    model=model,
                    api_key=api_key,
                    verbose=verbose,
                    semaphore=semaphore,
                )
                for agent, prompt in prompts
            )
        )


def _agent_prompts(
    agents: list[Agent],
    user_prompt: str,
    *,
    constitution: str | None,
    diff: str,
    styleguide: str | None,
    file_listing: str | None,
) -> list[tuple[Agent, str]]:
    """Pair each agent with its prompt; Constitution is skipped without a constitution."""
    prompts: list[tuple[Agent, str]] = []
    for agent in agents:
        if agent.slug != CONSTITUTION.slug:
            prompts.append((agent, user_prompt))
        elif constitution:
            prompts.append(
                (agent, build_constitution_prompt(diff, constitution, styleguide, file_listing))
            )
        else:
            print("Skipping Constitution agent: no constitution file found.")
    return prompts


async def run_review(
    *,
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
    required_low_agents: frozenset[str] = REQUIRED_LOW_AGENTS,
    incremental: bool = False,
    usage_json: str | None = None,
) -> int:
    """Run the full review pipeline.

    When comment=True, posts findings as a GitHub PR review after printing.
    Returns exit code (0 clean, 1 blocking, 2 usage error or an agent that
    could not finish). An unfinished agent outranks findings, except under
    --advisory: there it is reported, not raised, unless fewer than half of
    the agents finished. That is no review at all, and exits 2 regardless.
    LOWs from agents named in required_low_agents are exempt from the
    advisory LOW cap, so triage sees every LOW it requires an answer to.

    With incremental=True (and comment=True) on a PR, only the commits since
    the last complete Gatehouse review are reviewed; the given diff is the
    whole PR's, used as the fallback and to anchor comments.

    usage_json, when set, receives each agent's usage record and the total
    as JSON, written after the review is posted.
    """
    diff = stdin_diff if stdin_diff is not None else get_git_diff(base, staged)
    spec = parse_ignore(_load_context_file(IGNORE_FILE)) if diff.strip() else None
    diff, ignored = filter_diff(diff, spec)
    if ignored:
        print(f"Ignored {len(ignored)} file(s) per {IGNORE_FILE}.", file=sys.stderr)
    if not diff.strip():
        print("No changes to review.")
        return 0

    anchors, scope = None, ""
    if comment:
        anchors = right_side_lines(diff)
        diff, scope = await _review_scope(diff, spec, incremental=incremental)
    if not diff.strip():
        print("No changes to review since the last review.")
        return 0

    agents = get_agents(agent_slugs)
    styleguide = load_styleguide()
    file_listing = filter_listing(get_file_listing(), spec)
    user_prompt = build_user_prompt(diff, styleguide, file_listing)

    constitution = load_constitution(constitution_path)
    prompts = _agent_prompts(
        agents,
        user_prompt,
        constitution=constitution,
        diff=diff,
        styleguide=styleguide,
        file_listing=file_listing,
    )

    threads = asyncio.create_task(asyncio.to_thread(fetch_answered_threads)) if comment else None
    raw = await _run_agents(prompts, model=model, api_key=api_key, verbose=verbose)

    failed = tuple(
        (agent.name, reason or "unknown") for agent, findings, _, reason in raw if findings is None
    )
    all_results: Results = [
        (agent, findings) for agent, findings, _, _ in raw if findings is not None
    ]
    usage = [_usage_record(agent.slug, reply) for agent, _, reply, _ in raw if reply is not None]
    fallbacks = _report_usage(usage)

    haystack = _evidence_haystack(diff, styleguide, constitution)
    answered = await threads if threads else []
    all_results, dropped = _filter_findings(
        all_results, haystack, answered, required_low_agents, anchors
    )

    if all_results or not failed:  # with no agent finished, there are no results to call clean
        format_results(all_results)

    has_blocking = _has_blocking_findings(all_results)
    request_changes = has_blocking and not advisory
    exit_code = 1 if request_changes else 0
    if failed:
        names = ", ".join(f"{name} ({reason})" for name, reason in failed)
        print(f"Review incomplete: {names} could not finish.", file=sys.stderr)
        if not advisory or review_failed(len(failed), len(raw)):
            exit_code = 2
    print_summary(all_results, exit_code)

    if comment:
        await post_pr_review(
            all_results,
            request_changes,
            failed,
            len(ignored),
            **dropped,
            scope=scope,
            fallbacks=fallbacks,
            agent_count=len(raw),
            commit_id=os.environ.get("GATEHOUSE_HEAD_SHA", ""),
        )
    _save_usage(usage, usage_json)

    return exit_code
