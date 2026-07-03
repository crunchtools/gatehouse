# gatehouse

Local AI code review using 8 concurrent Gemini agents. Inspired by [diffray](https://github.com/nicepkg/diffray)'s multi-agent architecture and anti-noise prompting.

## Install

```bash
uv tool install gatehouse
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
gatehouse --model gemini-2.5-pro

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
| 2 | Usage error (missing API key, bad arguments) |

## Configuration

Set `GEMINI_API_KEY` environment variable. If `.gemini/styleguide.md` exists in the reviewed project, it is injected as context.

### Constitution Discovery

The Constitution agent auto-discovers a project constitution in priority order:

1. `--constitution <path>` (explicit override)
2. `.specify/memory/constitution.md` (spec-kit)
3. `AGENTS.md` (cross-runtime standard)
4. `CLAUDE.md` (Anthropic project instructions)

If no constitution file is found, the agent is silently skipped.

## License

AGPL-3.0-or-later
