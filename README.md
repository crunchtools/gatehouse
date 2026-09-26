# gatehouse

Local AI code review using 8 concurrent LLM agents, called through [OpenRouter](https://openrouter.ai). Inspired by [diffray](https://github.com/nicepkg/diffray)'s multi-agent architecture and anti-noise prompting.

## Install

```bash
uv tool install gatehouse-crunchtools
```

## Usage

```bash
# Review current branch vs main
gatehouse

# Review staged changes only
gatehouse --staged

# Review against a specific base
gatehouse --base develop

# Run specific agents only
gatehouse --agents bugs,security

# Use a different model
gatehouse --model google/gemini-3.8-flash

# Advisory mode (never exit non-zero)
gatehouse --advisory
```

## Agents

| Agent | Focus | Blocking |
|-------|-------|----------|
| Bug Hunter | Null safety, logic errors, edge cases, async bugs, resource leaks | Yes (high/critical) |
| Security Scan | Injection, auth bypass, hardcoded secrets, data exposure | Yes (always) |
| Performance Check | O(n^2), N+1 queries, memory leaks, blocking I/O | Yes (high/critical) |
| Test Coverage | Missing unit/integration tests, untested edge cases and APIs | Advisory only |
| Documentation | Missing/stale docstrings, undocumented public APIs | Yes (critical/high) |
| Constitution | Violations of project constitution/spec rules | Yes (critical/high) |
| Consistency Check | Naming patterns, API consistency, error handling patterns | Advisory only |
| General Review | Over-abstraction, unclear naming, hidden dependencies | Advisory only |

All 8 agents run concurrently. Findings below 80% confidence are filtered out.

## How Agents See Changes

Since v0.3.0, agents do not receive raw unified diffs. Each hunk is rendered
as a structured view: a BEFORE block (the old code, removed lines marked
`[-]`) and an AFTER block (the new code with real file line numbers, added
lines marked `[+]`), plus instructions to judge the direction of a change.
Protections *added* by a change are treated as fixes, not findings;
protections *removed* by a change are flagged. Diffs that cannot be parsed
fall back to the raw unified format.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No issues or advisory-only findings |
| 1 | Blocking findings detected (critical/high) |
| 2 | Usage error (missing API key, bad arguments), or an agent could not finish (reported, but exit 0, under `--advisory`) |

## GitHub Actions

Drop in [`examples/gatehouse.yml`](examples/gatehouse.yml) to review every PR (forks included) via the reusable `review.yml` workflow — the diff is piped as data, never checked out or executed.

The review is **advisory by default**: findings post as PR comments and the check always passes (an agent that could not finish is named in the review instead of failing it), so a non-deterministic LLM finding can never block a merge. Do not mark it a required status check. To let critical/high findings fail the check (still not recommended as a required gate), opt in:

```yaml
uses: crunchtools/gatehouse/.github/workflows/review.yml@v0.10.0
with:
  blocking: true
```

## Configuration

Set `OPENROUTER_API_KEY`, or `OPENROUTER_API_KEY_FILE` pointing at a file that holds the key (the file wins when both are set). Either can live in `~/.config/mcp-env/gatehouse.env`. If `.gemini/styleguide.md` exists in the reviewed project, it is injected as context.

### Model

The default model is `openai/gpt-6-luna`, with `google/gemini-3.1-flash-lite` as an automatic fallback when it is rate-limited or down. Every request requires zero data retention and forbids training on prompts, so only providers that keep nothing may serve it. `--model` takes any OpenRouter slug; the fallback still applies.

Luna was chosen on a 33-diff replay of real crunchtools changes (see crunchtools RT #1505): it caught as many reintroduced bugs as `gemini-2.5-flash`, with far less noise on clean PRs, at about a sixth of the cost.

Upgrading from 0.8.x: gatehouse no longer reads `GEMINI_API_KEY`. Replace it with `OPENROUTER_API_KEY` in your env file and GitHub secrets.

### Constitution Discovery

The Constitution agent auto-discovers a project constitution in priority order:

1. `--constitution <path>` (explicit override)
2. `.specify/memory/constitution.md` (spec-kit)
3. `AGENTS.md` (cross-runtime standard)
4. `CLAUDE.md` (Anthropic project instructions)

If no constitution file is found, the agent is silently skipped.

## License

AGPL-3.0-or-later
