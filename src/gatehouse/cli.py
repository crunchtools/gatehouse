"""CLI entry point for gatehouse."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from gatehouse.gemini import DEFAULT_MODEL
from gatehouse.review import run_review


def main() -> None:
    """Main entry point."""
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
        default="main",
        help="Base branch to diff against (default: main)",
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
            "Error: GEMINI_API_KEY environment variable not set",
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
