# gatehouse Constitution

> **Version:** 1.3.0
> **Ratified:** 2026-04-04
> **Amended:** 2026-10-02
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.18.0
> **Profile:** CLI Tool

This file holds gatehouse's own rules. The fleet rules and the CLI Tool
profile (license, test standards, Gourmand, quality gates) apply at the
inherited version and are checked against this repo's files by
`constitution.yml`. They are not restated here.

## Purpose

Local AI code review CLI that analyzes git diffs using 8 concurrent LLM agents with anti-noise prompting and confidence-based filtering.

## Versioning

Semantic Versioning 2.0.0. MAJOR for CLI interface changes, MINOR for new agents or features, PATCH for bug fixes.

## CLI Interface

Built with argparse. Flags: `--staged`, `--stdin`, `--base`, `--agents`, `--model`, `--constitution`, `--advisory`, `--comment`, `--verbose`.

Exit code `0` on success or advisory-only findings. Exit code `1` on blocking findings (critical/high severity from blocking agents). Exit code `2` on usage errors (missing API key, bad arguments) or when an agent could not finish, so an outage never reads as a clean review. Under `--advisory` an unfinished agent is reported in stderr and in the posted review, and the exit code stays `0`.

The blocking exit code applies to **local** invocation. The reusable CI workflow (`.github/workflows/review.yml`) is **advisory by default**: it passes `--advisory` so the check always exits `0` and posts findings as a plain `COMMENT` review, never gating a merge. Callers opt into blocking with the `blocking: true` input, and even then the check MUST NOT be marked a required status check.

The triage workflow (`.github/workflows/triage.yml`) is the opposite case and is kept separate on purpose. It never judges code — it fails only while a required inline finding has no reply from someone other than the reviewer — so it is deterministic and SHOULD be marked a required status check. Required means CRITICAL, HIGH or MEDIUM from any agent, or LOW from an agent named in its `required_low_agents` input (Bug Hunter and Security Scan by default); the filter reads only the severity and agent name printed at the start of each finding. That is what makes an advisory reviewer's findings a merge condition without giving the LLM a vote.

## External APIs

Calls OpenRouter chat completions at `openrouter.ai/api/v1/chat/completions` via httpx async client. Credential: `OPENROUTER_API_KEY` (or a file named by `OPENROUTER_API_KEY_FILE`). Default model: `openai/gpt-6-luna`, with `google/gemini-3.1-flash-lite` as fallback. Every request requires zero data retention and forbids training on prompts.

## Agent Architecture

Eight agents run concurrently via `asyncio.gather`; high/critical findings from a blocking agent block:

| Agent | Slug | Blocking |
|-------|------|----------|
| Bug Hunter | bugs | Yes |
| Security Scan | security | Yes |
| Performance Check | performance | Yes |
| Test Coverage | tests | Advisory only |
| Documentation | docs | Yes |
| Constitution | constitution | Yes (skipped without a constitution file) |
| Consistency Check | consistency | Advisory only |
| General Review | general | Advisory only |

Findings filtered at confidence >= 80%.

## Ignored Paths

`.gatehouse-ignore` (gitignore syntax, repo root) removes matching files from the diff and file listing before review. Under the fork-safe workflow it MUST be loaded from the trusted base, never the PR head. A change to `.gatehouse-ignore` itself is always reviewed, and the posted review states how many files were skipped.

## Container

Built on the `quay.io/hummingbird/python:latest-fips-builder` image (git is needed at runtime) with a venv. Published to `quay.io/crunchtools/gatehouse` and `ghcr.io/crunchtools/gatehouse`.

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-04-04 | Initial constitution (gatehouse 0.1.0) |
| 1.1.0 | 2026-09-23 | Triage workflow as the required check (0.8.0) |
| 1.2.0 | 2026-09-29 | LOW findings triaged by agent (0.14.0) |
| 1.3.0 | 2026-10-02 | Manifest under constitution v1.18.0: License, Testing, Gourmand and Quality Gates restatements removed |
