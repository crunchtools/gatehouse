"""CLI entry point for gatehouse."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from gatehouse.gemini import DEFAULT_MODEL
from gatehouse.review import run_review

ENV_FILE = Path.home() / ".config" / "mcp-env" / "gatehouse.env"


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from an env file into os.environ.

    Existing environment variables take precedence (are not overwritten).
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
        if key not in os.environ:
            os.environ[key] = value


def main() -> None:
    """Main entry point."""
    load_env_file(ENV_FILE)

    parser = argparse.ArgumentParser(
        prog="gatehouse",
        description="Local AI code review using Gemini agents",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Review staged changes only",
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
        help=f"Gemini model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Advisory mode: never exit non-zero",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show raw API responses on stderr",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(
            "Error: GEMINI_API_KEY not set",
            file=sys.stderr,
        )
        print(
            f"  Set in environment or add to {ENV_FILE}",
            file=sys.stderr,
        )
        sys.exit(2)

    agent_slugs: list[str] | None = (
        args.agents.split(",") if args.agents else None
    )

    exit_code = asyncio.run(
        run_review(
            base=args.base,
            staged=args.staged,
            agent_slugs=agent_slugs,
            model=args.model,
            advisory=args.advisory,
            verbose=args.verbose,
            api_key=api_key,
        )
    )
    sys.exit(exit_code)
