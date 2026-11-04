# Gatehouse

Local AI code review CLI using 8 concurrent LLM agents via OpenRouter.

## Quick Start

```bash
# Development
cd ~/Projects/crunchtools/gatehouse
uv sync --extra dev

# Run from a git repo with changes
OPENROUTER_API_KEY=your_key gatehouse
gatehouse --staged
gatehouse --agents bugs,security
gatehouse --advisory
gatehouse --usage-json usage.json   # per-agent model/tokens/cost
gatehouse --samples 1               # single-sample review (pre-0.21.0 behaviour)
```

## Architecture

- `src/gatehouse/cli.py` -- argparse entry point
- `src/gatehouse/agents.py` -- 8 agent prompt definitions
- `src/gatehouse/config.py` -- `gatehouse.toml` over the packaged `defaults.toml`; sample counts and vote thresholds
- `src/gatehouse/consensus.py` -- clustering across samples by evidence containment, and the vote tally
- `src/gatehouse/diffview.py` -- unified diff to BEFORE/AFTER view for agents
- `src/gatehouse/llm.py` -- httpx async OpenRouter client (Luna default, Flash Lite fallback, ZDR-only); returns a `Completion` with serving model and usage
- `src/gatehouse/github.py` -- PR review posting, answered threads, last reviewed commit and compare diff for `--incremental`
- `src/gatehouse/review.py` -- orchestration and exit code logic
- `src/gatehouse/ignore.py` -- `.gatehouse-ignore` (gitignore syntax) filtering of diff and file listing
- `src/gatehouse/output.py` -- terminal output with ANSI colors

## Agents

| Agent | Slug | Blocking |
|-------|------|----------|
| Bug Hunter | bugs | Yes |
| Security Scan | security | Yes |
| Performance Check | performance | Yes |
| Test Coverage | tests | No |
| Documentation | docs | Yes |
| Constitution | constitution | Yes |
| Consistency Check | consistency | No |
| General Review | general | No |

## Development Commands

```bash
uv run ruff check src tests        # Lint
uv run mypy src                    # Type check
uv run pytest -v                   # Tests (mocked API)
gourmand --full .                  # AI slop detection
podman build -f Containerfile .    # Container build
```

## Consensus

Each agent reviews the diff `samples` times and the samples vote: `block` votes
(default: all of them) to fail the run on a critical/high from a blocking agent,
`report` votes (default: a majority) to be shown at all. Configured in
`gatehouse.toml`, which CI reads from the base branch. `--samples 1` reproduces
the old single-sample gate.

## Exit Codes

- 0: No issues or advisory-only
- 1: Blocking findings (critical/high from blocking agents)
- 2: Usage error (missing API key, bad args), or an agent could not finish (exit 0 under --advisory, unless fewer than half of the agents finished)
