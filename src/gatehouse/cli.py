"""CLI entry point for gatehouse."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from gatehouse.llm import DEFAULT_MODEL
from gatehouse.review import run_review

ENV_FILE = Path.home() / ".config" / "mcp-env" / "gatehouse.env"


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from an env file into os.environ.

    Existing environment variables take precedence (are not overwritten).
    Keys must match the POSIX portable character set ([A-Za-z_][A-Za-z0-9_]*);
    invalid keys are silently skipped.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if key not in os.environ:
            os.environ[key] = value


def load_api_key() -> str:
    """Return the OpenRouter key; OPENROUTER_API_KEY_FILE wins over the variable.

    Warns (does not fail) when the key file is readable by group or others.
    """
    key_file = os.environ.get("OPENROUTER_API_KEY_FILE")
    if key_file:
        path = Path(key_file)
        if path.stat().st_mode & 0o077:
            print(
                f"Warning: {path} is readable by group or others; chmod 600 it",
                file=sys.stderr,
            )
        return path.read_text().strip()
    return os.environ.get("OPENROUTER_API_KEY", "")


def main() -> None:
    """Main entry point."""
    load_env_file(ENV_FILE)

    parser = argparse.ArgumentParser(
        prog="gatehouse",
        description="Local AI code review using concurrent LLM agents",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Review staged changes only",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read diff from stdin instead of running git diff",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Base branch to diff against (default: auto-detect)",
    )
    parser.add_argument(
        "--agents",
        type=str,
        default=None,
        help="Comma-separated agent slugs: bugs,security,performance,consistency,general",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model slug (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--constitution",
        type=str,
        default=None,
        help="Path to constitution file (default: auto-discover)",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Advisory mode: never exit non-zero",
    )
    parser.add_argument(
        "--comment",
        action="store_true",
        help="Post findings as GitHub PR review comments (requires gh CLI)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show raw API responses on stderr",
    )
    args = parser.parse_args()

    api_key = load_api_key()
    if not api_key:
        print(
            "Error: OPENROUTER_API_KEY not set",
            file=sys.stderr,
        )
        print(
            f"  Set it (or OPENROUTER_API_KEY_FILE) in the environment or in {ENV_FILE}",
            file=sys.stderr,
        )
        if os.environ.get("GEMINI_API_KEY"):
            print(
                "  GEMINI_API_KEY is no longer used: gatehouse calls OpenRouter since 0.9.0",
                file=sys.stderr,
            )
        sys.exit(2)

    agent_slugs: list[str] | None = args.agents.split(",") if args.agents else None

    stdin_diff: str | None = None
    if args.stdin:
        stdin_diff = sys.stdin.read()

    exit_code = asyncio.run(
        run_review(
            base=args.base,
            staged=args.staged,
            stdin_diff=stdin_diff,
            agent_slugs=agent_slugs,
            model=args.model,
            advisory=args.advisory,
            verbose=args.verbose,
            api_key=api_key,
            constitution_path=args.constitution,
            comment=args.comment,
        )
    )
    sys.exit(exit_code)
