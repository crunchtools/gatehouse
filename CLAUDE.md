# Gatehouse

Local AI code review CLI using 5 concurrent Gemini agents.

## Quick Start

```bash
# Development
cd ~/Projects/crunchtools/gatehouse
uv sync --extra dev

# Run from a git repo with changes
GEMINI_API_KEY=your_key gatehouse
gatehouse --staged
gatehouse --agents bugs,security
gatehouse --advisory
```

## Architecture

- `src/gatehouse/cli.py` -- argparse entry point
- `src/gatehouse/agents.py` -- 5 agent prompt definitions
- `src/gatehouse/gemini.py` -- httpx async Gemini REST client
- `src/gatehouse/review.py` -- orchestration and exit code logic
- `src/gatehouse/output.py` -- terminal output with ANSI colors

## Agents

| Agent | Slug | Blocking |
|-------|------|----------|
| Bug Hunter | bugs | Yes |
| Security Scan | security | Yes |
| Performance Check | performance | Yes |
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

## Exit Codes

- 0: No issues or advisory-only
- 1: Blocking findings (critical/high from blocking agents)
- 2: Usage error (missing API key, bad args)
