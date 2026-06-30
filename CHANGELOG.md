# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and Semantic Versioning.

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
