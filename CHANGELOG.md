# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and Semantic Versioning.

## [0.3.0] - 2026-07-02

### Added
- **Structured BEFORE/AFTER diff view** (`src/gatehouse/diffview.py`): agents
  no longer receive raw unified diffs. Each hunk is rendered as an explicit
  BEFORE block (old code, removed lines marked `[-]`) and AFTER block (new
  code with real file line numbers, added lines marked `[+]`), plus a preamble
  teaching agents to judge change direction. Fixes #19: hardening added by a
  PR was flagged as a finding, while a diff removing a security control could
  pass unnoticed. Removed lines stay visible so deleted protections are
  flagged. Falls back to the raw diff if parsing fails (e.g. exotic `--stdin`
  input).

### Changed
- `ANTI_NOISE` directive now states both directions explicitly: protections
  added by a change are fixes (not findings); protections removed by a change
  ARE findings.

## [0.2.0] - 2026-06-30

### Added
- **Fork-safe PR review** via a hardened, reusable `pull_request_target` workflow
  (`.github/workflows/review.yml`): reviews fork PRs with a bring-your-own Gemini
  key and zero hosting. The diff is fetched over the API and piped to
  `gatehouse --stdin` — nothing is ever checked out, so the PR's code is never
  executed.
- **Trusted-base context fetch**: `load_styleguide`/`load_constitution` now load
  the styleguide and constitution from the base repo over the GitHub API (via the
  new `GATEHOUSE_CONTEXT_REPO`/`GATEHOUSE_CONTEXT_REF` env vars) instead of from a
  checkout, so a fork cannot inject prompt-injecting rule files.
- **Workflow-protection guard** (`.github/workflows/gatehouse.yml`): rejects PRs
  from outside contributors that modify `.github/workflows/`.
- `examples/gatehouse.yml`: drop-in template combining guard + review for adopters.
- `tests/test_fork_safety.py`: adversarial regression tests for the trust boundary.

### Changed
- Gatehouse code review now runs on **all** PRs (internal and fork) via
  `gatehouse.yml` on `pull_request_target`, replacing the inline `pull_request`
  job in `ci.yml`.

## [0.1.0] - 2026-04-04

### Added
- Initial release: local AI code review CLI with concurrent Gemini agents,
  confidence filtering, container distribution, and `--comment` PR reviews.
