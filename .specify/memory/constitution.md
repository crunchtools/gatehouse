# gatehouse Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-04-04
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.5.0
> **Profile:** CLI Tool

## Purpose

Local AI code review CLI that analyzes git diffs using 5 concurrent Gemini agents with anti-noise prompting and confidence-based filtering.

## License

AGPL-3.0-or-later

## Versioning

Semantic Versioning 2.0.0. MAJOR for CLI interface changes, MINOR for new agents or features, PATCH for bug fixes.

## CLI Interface

Built with argparse. Flags: `--staged`, `--base`, `--agents`, `--model`, `--advisory`, `--verbose`.

Exit code `0` on success or advisory-only findings. Exit code `1` on blocking findings (critical/high severity from blocking agents). Exit code `2` on usage errors (missing API key, bad arguments).

## External APIs

Calls Gemini REST API at `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` via httpx async client. Credential: `GEMINI_API_KEY` environment variable. Default model: `gemini-2.5-flash`.

## Agent Architecture

Five agents run concurrently via `asyncio.gather`:

| Agent | Slug | Blocking |
|-------|------|----------|
| Bug Hunter | bugs | Yes (high/critical) |
| Security Scan | security | Yes (always) |
| Performance Check | performance | Yes (high/critical) |
| Consistency Check | consistency | Advisory only |
| General Review | general | Advisory only |

Findings filtered at confidence >= 80%.

## Container

Built on `quay.io/hummingbird/python:latest-fips` base image with multi-stage venv pattern. Published to `quay.io/crunchtools/gatehouse`.

## Testing

All Gemini API calls mocked with httpx. Tests run via `uv run pytest -v`. Exit code contract verified in integration tests.

## Gourmand

Zero violations required. Config in `gourmand.toml`, exceptions in `gourmand-exceptions.toml`.

## Quality Gates

1. Lint -- `uv run ruff check src tests`
2. Type Check -- `uv run mypy src`
3. Tests -- `uv run pytest -v`
4. Gourmand -- `gourmand --full .`
5. Container Build -- `podman build -f Containerfile .`
