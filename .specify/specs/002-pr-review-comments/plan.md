# Plan: 002-pr-review-comments

> **Status:** Planning
> **Spec:** [002-pr-review-comments](spec.md)

## Summary

Add `--comment` flag that posts findings as a GitHub PR review using `gh api`. New `github.py` module handles PR context detection and review submission. Changes to `cli.py` (new flag), `review.py` (pass comment flag + results to github module).

## Files to Create

1. **`src/gatehouse/github.py`** — PR context detection and review submission via `gh api`
2. **`tests/test_github.py`** — Tests for PR context detection and review formatting

## Files to Modify

1. **`src/gatehouse/cli.py`** — Add `--comment` flag, pass to `run_review`
2. **`src/gatehouse/review.py`** — Accept `comment` param, call `post_pr_review` after formatting results

## Implementation Steps

1. Create `github.py` with:
   - `detect_pr_context()` — reads `GITHUB_REPOSITORY`, `GITHUB_REF` or `GITHUB_EVENT_PATH` to extract owner/repo and PR number
   - `format_review_comment(finding)` — maps a finding dict to `{path, line, body}` for GitHub API
   - `post_pr_review(findings, results, has_blocking)` — submits a single PR review via `gh api`
2. Add `--comment` flag to `cli.py`, pass through to `run_review`
3. In `review.py`, after `format_results` and exit code calculation, call `post_pr_review` if `comment=True`
4. Tests: mock subprocess for `gh api` calls, test PR context detection from env vars, test comment formatting

## Testing Strategy

- Mock `subprocess.run` for `gh api` calls (same pattern as git diff mocking)
- Test `detect_pr_context` with various env var combinations
- Test `format_review_comment` produces correct markdown body
- Test `post_pr_review` constructs correct API payload
- Test graceful failure when not in a PR context (warn, don't crash)
