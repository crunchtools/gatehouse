# gatehouse Constitution

> **Version:** 1.8.0
> **Ratified:** 2026-04-04
> **Amended:** 2026-11-04
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.31.0
> **Profile:** CLI Tool

This file holds gatehouse's own rules. The fleet rules and the CLI Tool
profile (license, test standards, Gourmand, quality gates) apply at the
inherited version and are checked against this repo's files by
`constitution.yml`. They are not restated here.

## Purpose

Local AI code review CLI that analyzes git diffs using 8 concurrent LLM agents with anti-noise prompting, confidence-based filtering and consensus voting across samples.

## Versioning

Semantic Versioning 2.0.0. MAJOR for CLI interface changes, MINOR for new agents or features, PATCH for bug fixes.

## CLI Interface

Built with argparse. Flags: `--staged`, `--stdin`, `--base`, `--agents`, `--model`, `--constitution`, `--advisory`, `--comment`, `--samples`, `--report-votes`, `--verbose`.

Exit code `0` on success or advisory-only findings. Exit code `1` on blocking findings (critical/high severity from blocking agents). Exit code `2` on usage errors (missing API key, bad arguments) or when an agent could not finish, so an outage never reads as a clean review. Under `--advisory` an unfinished agent is reported in stderr and in the posted review, with the class of its failure, and the exit code stays `0` while at least half of the agents finished. Fewer than half is not a review: the exit code is `2` regardless of `--advisory`, and the posted review says how few agents finished, never "found no issues" alone, and carries a hidden failed marker.

The blocking exit code applies to **local** invocation. The reusable CI workflow (`.github/workflows/review.yml`) is **advisory by default**: it passes `--advisory` so no finding fails the check, and posts findings as a plain `COMMENT` review, never gating a merge. When it cannot run the reviewer at all (no diff, no key, no image) it posts a review carrying the failed marker and fails. Callers opt into blocking with the `blocking: true` input, and even then the check MUST NOT be marked a required status check.

The triage workflow (`.github/workflows/triage.yml`) is the opposite case and is kept separate on purpose. It never judges code — it fails only while a required inline finding has no reply from someone other than the reviewer, or while the newest Gatehouse review carries the failed marker — so it is deterministic and SHOULD be marked a required status check. Required means CRITICAL, HIGH or MEDIUM from any agent, or LOW from an agent named in its `required_low_agents` input (Bug Hunter and Security Scan by default); the filter reads only the severity and agent name printed at the start of each finding. That is what makes an advisory reviewer's findings a merge condition without giving the LLM a vote.

## External APIs

Calls OpenRouter chat completions at `openrouter.ai/api/v1/chat/completions` via httpx async client. Credential: `OPENROUTER_API_KEY` (or a file named by `OPENROUTER_API_KEY_FILE`). Default model: `openai/gpt-6-luna`, with `google/gemini-3.1-flash-lite` as fallback. Every request requires zero data retention and forbids training on prompts.

## Agent Architecture

Eight agents run concurrently via `asyncio.gather`, each over `n` samples (see Consensus); high/critical findings from a blocking agent block when they reach the blocking vote threshold:

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

Findings filtered at confidence >= 80%, per sample, before any vote is counted.

## Consensus

Each agent runs `n` samples concurrently. Every sample's findings are unioned for recall, clustered, then tallied additively — no cross-penalties, no weighting, no synthesizing model. A cluster is one finding with a vote count.

Findings cluster on agent, category, file and **evidence containment**: two findings cluster when one's whitespace-stripped evidence contains the other's. Never a hash of the evidence — two samples quoting different spans of one bug would never cluster, and a unanimity threshold would never fire. No line number takes part, so a cluster survives reformatting and line drift. A finding with no usable evidence does not cluster and carries at most one vote, so it can be reported but can never block.

A cluster blocks only when its votes reach the `block` threshold, its severity is critical or high, and its agent is blocking. At `report` it is shown. The report threshold is `--report-votes` when it is given, else `GATEHOUSE_REPORT_VOTES`, else 1 under `--verbose`, else the configured value: so `--verbose` alone reports every finding one sample saw, and an explicit threshold is never overridden by it. Thresholds resolve against the samples that **finished**: an agent counts as unfinished only when fewer than a majority of its `n` finished, because a timeout is not a dissent.

Per-sample filters (unverifiable evidence, disproved refs) run before the tally; cluster-level filters (off-diff lines, re-raises, the advisory LOW cap) run after it.

## Configuration

A packaged `defaults.toml` holds the shipped sample counts and thresholds; an optional `gatehouse.toml` at the reviewed repository's root overlays it key by key, under `[consensus]` (`samples`, `block`, `report`) and `[consensus.agents]` (per-agent sample counts). `block` and `report` take `"all"`, `"majority"` or an integer, resolved against that agent's own `n`.

Precedence: `--samples` > `GATEHOUSE_SAMPLES` > `gatehouse.toml` > packaged defaults, and for the report threshold `--report-votes` > `GATEHOUSE_REPORT_VOTES` > 1 under `--verbose` > `gatehouse.toml` > packaged defaults. Per-agent counts live in the file alone. Per-agent `n` MUST be in [1, 5] and one review MUST NOT exceed 40 calls; an out-of-range count is a usage error (exit 2), never a silent clamp. An integer threshold above an agent's `n` can never be reached, which would silence that agent, so it is a usage error too — with two exceptions. `block` for an advisory agent resolves no blocking threshold at all, because it cannot block what it finds. And the `--report-votes` override clamps to the agent's own `n` instead of failing: the file sets a per-agent count beside its threshold, so an unreachable value there is a mistake worth exit 2, while one number passed on the command line MUST hold for all eight agents at once, three of which run a single sample. The clamp is permitted only for `report`, and only from the override — never from the file, and never for `block`. `--samples 1` restores single-sample thresholds; a critical/high finding with no usable evidence still MUST NOT fail the run.

Under the fork-safe workflow `gatehouse.toml` MUST be loaded from the trusted base, like `.gatehouse-ignore` and the styleguide: a pull request MUST NOT be able to weaken the gate that reviews it.

## Ignored Paths

`.gatehouse-ignore` (gitignore syntax, repo root) removes matching files from the diff and file listing before review. Under the fork-safe workflow it MUST be loaded from the trusted base, never the PR head. A change to `.gatehouse-ignore` itself is always reviewed, and the posted review states how many files were skipped.

## Finding Ledger

`gatehouse-findings.toml` (repo root, committed) records findings already judged. Status MUST be `not-a-bug` or `deferred`; a deferred entry's reason MUST link the issue tracking it, and any reason MUST reach the 20 characters the triage check requires of a reply. There is no `fixed` status: an entry applies while the evidence it quotes is still present and is reported stale once it is gone, so a suppression cannot outlive the code it covered.

Gatehouse MUST NOT write this file. The pre-commit hook mounts the reviewed repository read-only; gatehouse prints entries ready to paste and the author commits them. A malformed entry MUST be named and ignored, never fatal: a ledger can only make a review say less.

Entries match findings by agent, category, file and evidence containment. A finding with no usable evidence MUST NOT be matched, so it can never be suppressed.

Under the fork-safe workflow the ledger is loaded from the pull request's head, because it describes that branch's own code — except when the head is a fork, where it MUST be loaded from the base instead: a fork MUST NOT be able to suppress the findings on its own pull request. An entry present on the head and not on the base MUST be posted as a finding requiring a triage answer.

## Container

Built on the `quay.io/hummingbird/python:3.14-fips-builder` image (git is needed at runtime) with a venv. Published to `quay.io/crunchtools/gatehouse` and `ghcr.io/crunchtools/gatehouse`.

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-04-04 | Initial constitution (gatehouse 0.1.0) |
| 1.1.0 | 2026-09-23 | Triage workflow as the required check (0.8.0) |
| 1.2.0 | 2026-09-29 | LOW findings triaged by agent (0.14.0) |
| 1.3.0 | 2026-10-02 | Manifest under constitution v1.18.0: License, Testing, Gourmand and Quality Gates restatements removed |
| 1.4.0 | 2026-10-10 | A review fewer than half of whose agents finished fails, under `--advisory` too, and fails triage (0.16.0) |
| 1.5.0 | 2026-10-11 | Reusable workflows are called at the major tag, which a release moves after its images are pushed; a change needing a caller edit is a new major (0.17.0) |
| 1.6.0 | 2026-11-03 | Consensus voting over `n` samples per agent, clustering by evidence containment, and `gatehouse.toml` configuration loaded from the trusted base (0.21.0, spec 003) |
| 1.7.0 | 2026-11-03 | The finding ledger: `gatehouse-findings.toml`, read-only, matched by containment, stale once its quoted code is gone, head-side except on a fork (0.22.0, spec 003) |
| 1.8.0 | 2026-11-04 | The report threshold has a precedence of its own (`--report-votes`, `GATEHOUSE_REPORT_VOTES`, 1 under `--verbose`, the file), `--verbose` reports every finding one sample saw, and the override clamps to an agent's own sample count — the one permitted clamp, never from the file and never for `block` (0.23.0, spec 003) |
