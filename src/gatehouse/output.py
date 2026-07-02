"""Formatted terminal output for review findings."""

from __future__ import annotations

import os
import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gatehouse.agents import Agent

COLORS = {
    "critical": "\033[1;31m",
    "high": "\033[31m",
    "medium": "\033[33m",
    "low": "\033[36m",
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}


def use_color() -> bool:
    """Check if color output should be used."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def color(name: str) -> str:
    """Return ANSI code if color is enabled, empty string otherwise."""
    if use_color():
        return COLORS.get(name, "")
    return ""


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\[.*?\x1b\\")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from untrusted text."""
    return _ANSI_RE.sub("", text)


def severity_rank(finding: dict[str, Any]) -> int:
    """Return sort rank for severity (lower is more severe)."""
    ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return ranks.get(finding.get("severity", "low"), 4)


def format_finding(finding: dict[str, Any]) -> str:
    """Format a single finding for terminal display."""
    severity = finding.get("severity", "low")
    file_path = finding.get("file", "unknown")
    line_start = finding.get("lineStart", 0)
    line_end = finding.get("lineEnd", 0)
    description = strip_ansi(finding.get("description", ""))
    suggestion = strip_ansi(finding.get("suggestion", ""))
    evidence = strip_ansi(finding.get("evidence", ""))
    confidence = finding.get("confidence", 0)

    sev_color = color(severity)
    rst = color("reset")
    dim = color("dim")

    location = f"{file_path}:{line_start}"
    if line_end > line_start:
        location = f"{file_path}:{line_start}-{line_end}"

    lines = [
        f"  {sev_color}{severity.upper()}{rst} {location}",
        f"    {description}",
    ]
    if suggestion:
        lines.append(f"    {dim}Suggestion: {suggestion}{rst}")
    if evidence:
        lines.append(f"    {dim}Evidence: {evidence}{rst}")
    lines.append(f"    {dim}Confidence: {confidence}%{rst}")
    return "\n".join(lines)


def format_results(
    results: list[tuple[Agent, list[dict[str, Any]]]],
) -> None:
    """Print formatted results for all agents."""
    total = sum(len(findings) for _, findings in results)
    if total == 0:
        print("No issues found.")
        return

    bld = color("bold")
    rst = color("reset")
    dim = color("dim")

    for agent, findings in results:
        if not findings:
            continue
        blocking_label = "" if agent.blocking else f" {dim}(advisory){rst}"
        count = len(findings)
        noun = "finding" if count == 1 else "findings"
        print(f"\n{bld}{agent.name}{rst} ({count} {noun}){blocking_label}")
        for finding in sorted(findings, key=severity_rank):
            print(format_finding(finding))


def print_summary(
    results: list[tuple[Agent, list[dict[str, Any]]]],
    exit_code: int,
) -> None:
    """Print a summary line with counts by severity."""
    bld = color("bold")
    rst = color("reset")

    counts: dict[str, int] = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    for _, findings in results:
        for finding in findings:
            sev = finding.get("severity", "low")
            if sev in counts:
                counts[sev] += 1

    total = sum(counts.values())
    if total == 0:
        return

    parts: list[str] = []
    for sev in ("critical", "high", "medium", "low"):
        if counts[sev] > 0:
            sev_color = color(sev)
            parts.append(f"{sev_color}{counts[sev]} {sev}{rst}")

    summary = ", ".join(parts)
    print(f"\n{bld}Summary:{rst} {total} findings ({summary})")

    if exit_code != 0:
        crit = color("critical")
        print(f"{crit}Exit: {exit_code} (blocking findings detected){rst}")
    else:
        dim = color("dim")
        print(f"{dim}Exit: 0{rst}")
