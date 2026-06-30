# Spec 002: Post Findings as GitHub PR Review Comments

> **Status:** Draft
> **Version:** 0.1.0
> **Author:** Scott McCarty
> **Date:** 2026-06-29
> **Issue:** [#5](https://github.com/crunchtools/gatehouse/issues/5)

## Overview

Add a `--comment` flag that posts Gatehouse findings as inline GitHub PR review comments. When running in GHA, this gives contributors immediate feedback on the specific lines — closing the gap between Gatehouse and Gemini Code Assist.

## User Stories

1. As a contributor submitting a PR, I want Gatehouse findings posted as inline comments on my PR so I can see issues without digging through GHA job logs.

2. As a maintainer, I want blocking findings to submit a `REQUEST_CHANGES` review so the PR status clearly shows it needs work.

3. As a CI operator, I want `--comment` to use the auto-provisioned `GITHUB_TOKEN` so no extra secrets configuration is needed.

## Behavior

When `--comment` is set:

1. Detect PR context from GHA environment (`GITHUB_REPOSITORY`, `GITHUB_REF`, or `GITHUB_EVENT_PATH`)
2. Run review as normal (terminal output unchanged)
3. Map each finding to a PR review comment: `{path, line, body}`
4. Submit as a single PR review via `gh api`
5. Review event: `COMMENT` if no blocking findings, `REQUEST_CHANGES` if blocking findings exist
6. Review body: summary line (e.g., "Gatehouse found 3 issues (1 high, 2 medium)")

## Authentication

Uses `GITHUB_TOKEN` (auto-available in GHA) via the `gh` CLI. No separate API key needed. The `gh` CLI handles auth transparently.

## Scope

- New `--comment` CLI flag in `cli.py`
- New `github.py` module for PR context detection and review submission
- Wire `--comment` through `review.py` to call `github.py` after results
- Tests for `github.py` (mocked subprocess calls)

## Out of Scope

- Local (non-GHA) PR commenting — can be added later
- Updating existing reviews on re-runs
- Commenting on individual commits (only PR reviews)
