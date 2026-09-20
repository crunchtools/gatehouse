# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and Semantic Versioning.

## [Unreleased]

## [0.7.0] - 2026-09-19

### Changed
- **The Gourmand gate tracks `quay.io/crunchtools/gourmand:latest` again.**
  0.6.0 pinned the image to `:1.0.0` as scaffolding for a staged fleet
  rollout: old config and new binary are mutually incompatible, so pinning
  kept unmigrated repos green while they were cut over one at a time. The
  fleet is migrating all at once instead, which makes the pin pure overhead —
  every adopter would carry a pin that has to be removed again afterwards.
- **`.pre-commit-config.yaml` now runs `check --full`.** The hook was missed
  when this repo migrated its own CI in 0.6.0. It kept invoking the old
  `:latest` binary with pre-subcommand syntax against an already-migrated
  config, which trips the v0.1.0 zero-threshold bug: every threshold silently
  becomes 0. Locally that reported 139 bogus `implicit_state_machine`
  violations while CI was green.

### Fixed
- **`pyproject.toml` and `__init__.py` now agree with the tag.** v0.6.0 was
  tagged while both still read `0.5.0` — the same defect called out in the
  0.5.0 entry below, reintroduced.

## [0.6.0] - 2026-09-19

### Changed
- **Gourmand upgraded from upstream v0.1.0 to v0.16.5** (RT #1482). The gate
  had been pinned to a four-month-old revision; upstream had moved ~4,000
  commits and grown from 35 checks to 89.
- **`gourmand --full` is now `gourmand check --full`.** 0.16.5 is
  subcommand-based, so the bare form errors. This is why adopters must bump
  their `uses:` ref rather than inherit the change silently.

### Breaking
- Adopters on `@v0.5.0` must migrate their `gourmand.toml` in the same commit
  that bumps the ref: delete the `[thresholds]` block (0.16.5 restores correct
  built-in defaults, so the fleet-wide workaround is obsolete and now a hard
  error), and add a `classification` to every `[[exceptions]]` entry.

## [0.5.0] - 2026-09-19

### Changed
- **The PyPI distribution is now `gatehouse-crunchtools`.** The bare name
  `gatehouse` belongs to an unrelated project on PyPI, so `publish.yml` had
  never once succeeded and could not — this repo has never been distributed.
  The new name follows the same `-crunchtools` suffix every other Python
  package in the fleet uses, and exists for exactly this reason. The installed
  command is unchanged: still `gatehouse`. Install with
  `uv tool install gatehouse-crunchtools`.
- **Version bumped to 0.5.0 in `pyproject.toml` and `__init__.py`.** The v0.5.0
  tag was created while both still read 0.4.0, so the tag pointed at code
  calling itself a different version. The tag has been moved to the commit that
  agrees with it.

### Added
- **Reusable Gourmand workflow** (`.github/workflows/gourmand.yml`): the
  Gourmand CI gate is now a proper `workflow_call` reusable workflow instead
  of a copy-paste job body in `examples/gourmand.yml`. Fixes the "fix-once"
  propagation gap identified in RT #1468 — a dead `cargo install` gate kept
  regenerating fleet-wide because fixes to the old copy-paste example never
  reached repos that had already pasted it. Adopters now reference
  `crunchtools/gatehouse/.github/workflows/gourmand.yml@v0.5.0` instead of
  inlining the job.

### Changed
- `examples/gourmand.yml` now shows the one-line `uses:` reference instead of
  the full job body.

## [0.4.0] - 2026-07-06

### Added
- **`blocking` input on the reusable review workflow** (`.github/workflows/review.yml`):
  opt in to failing the check on critical/high findings. Defaults to `false`.

### Changed
- **Advisory by default in CI** (#23): the reusable review workflow now passes
  `--advisory` unless `blocking: true`, so the check always exits `0` and the
  review is posted as a plain `COMMENT` instead of `REQUEST_CHANGES`. A
  non-deterministic LLM finding can no longer block a merge. Local CLI behavior
  is unchanged (still exits `1` on blocking findings). The `examples/gatehouse.yml`
  guidance no longer tells adopters to mark the review job a required check.

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
