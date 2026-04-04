# Gatehouse Style Guide

## Architecture

Gatehouse is a Python CLI tool with 5 concurrent Gemini agents for code review.

### Module Structure
- `cli.py` -- argparse entry point
- `agents.py` -- agent prompt definitions (frozen dataclasses)
- `gemini.py` -- httpx async client for Gemini REST API
- `review.py` -- orchestration (git diff, concurrent agents, exit codes)
- `output.py` -- formatted terminal output with ANSI colors

### Conventions
- Python 3.11+, strict mypy, ruff linting
- httpx for HTTP (no vendor SDKs)
- argparse (stdlib) for CLI (no click/typer)
- All API calls mocked in tests (no live calls in CI)
- Exit 0 = success/advisory, Exit 1 = blocking findings, Exit 2 = usage error
- Respect NO_COLOR environment variable
- Structured output to stdout, diagnostics to stderr

### What NOT to Flag
- The agent prompt strings are intentionally long -- do not suggest splitting them
- The `dict[str, Any]` types for JSON findings are intentional -- no Pydantic models needed here
- subprocess calls to git are safe and expected
