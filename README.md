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

# Write per-agent model, token and cost usage as JSON
gatehouse --usage-json usage.json

# Review the diff once per agent instead of three times (pre-0.21.0 behaviour)
gatehouse --samples 1
```

Each agent logs one line to stderr with the model that served it, its token counts and cost, and whether a fallback model answered; a total line follows. `--usage-json` writes the same data as `{"agents": [...], "total": {...}}`: each agent record has `agent`, `model`, `prompt`, `completion`, `reasoning`, `cost` (USD) and `fallback`; the total has `calls`, the summed tokens and cost, and the `fallback` count.

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

All 8 agents run concurrently, each over several samples (see Consensus). Findings below 80% confidence are filtered out of every sample, and so are findings whose quoted evidence is not in the diff. At most 5 LOW findings from advisory agents (all but Bug Hunter and Security Scan) are reported, the most confident first.

## Consensus

An LLM asked the same question twice does not always answer the same way, which is why a single-sample review could raise a new HIGH on every run and block a commit forever. Each agent therefore reviews the diff several times, and the samples vote.

Votes are tallied additively — nothing penalizes anything else. The shipped defaults sample the five blocking agents three times each and the three advisory agents once:

| | Votes needed | Blocking agents (3 samples) | Advisory agents (1 sample) |
|---|---|---|---|
| Fail the run (`block`) | every sample agrees | 3 of 3 | never — they cannot block |
| Be reported (`report`) | a majority agrees | 2 of 3 | 1 of 1 |
| Appear under `--verbose` only | fewer | 1 of 3 | — |

So for the agents that can stop a commit, a HIGH only one sample saw is not a blocked commit, a 2/3 HIGH is reported for a human to judge without stopping work, and a 3/3 HIGH fails the run. An advisory agent runs once by default, since a second opinion buys nothing where no opinion can block; give it `samples` in `gatehouse.toml` if you want its findings tallied too. Blocking votes count only the samples that called a finding critical or high: two MEDIUMs and one HIGH is three votes to report and one to block.

Findings from different samples are the same finding when the agent, category and file match and one's quoted evidence contains the other's — whitespace and line numbers take no part, so a reformatted or re-anchored quote still counts. Two findings from the *same* sample never merge: a sample that reported two findings found two things. A finding with nothing quotable stands alone with one vote and no blocking vote at all: unevidenced findings cannot add up to a unanimous one, and a claim with nothing to quote cannot fail a run even in a repository that configured one vote to block.

The agreement is printed with each finding (", agreed by 2 of 3 samples") and recorded in the hidden PR comment marker as `votes=2/3`.

An agent is unfinished only when fewer than a majority of its samples come back; a timeout is not a vote against a finding.

## How Agents See Changes

Since v0.3.0, agents do not receive raw unified diffs. Each hunk is rendered
as a structured view: a BEFORE block (the old code, removed lines marked
`[-]`) and an AFTER block (the new code with real file line numbers, added
lines marked `[+]`), plus instructions to judge the direction of a change.
Protections *added* by a change are treated as fixes, not findings;
protections *removed* by a change are flagged. Diffs that cannot be parsed
fall back to the raw unified format.

## Measuring it

A reviewer that is not repeatable is not worth leaving installed, so repeatability is measured rather than asserted:

```bash
gatehouse --consensus-json votes.json          # per-finding votes and thresholds
uv run python scripts/stability_eval.py        # review tests/corpus 3x, compare
uv run python scripts/stability_eval.py --samples 1   # the pre-consensus baseline
```

The eval compares finding sets by Jaccard index, keyed on agent, category, file and quoted code, so prose and line numbers do not affect it. A weekly `Stability` workflow prints the table into its step summary. It is never a required check: a number that moves with model sampling must not gate a merge.

Two module-sized diffs, three runs each, at 0.22.0: **0.22** mean stability at one sample, **0.38** with consensus. Voting roughly doubles repeatability; the ledger is what makes a judged finding stay judged.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No issues or advisory-only findings |
| 1 | Blocking findings detected (critical/high) |
| 2 | Usage error (missing API key, bad arguments), or an agent could not finish. Under `--advisory` a minority of unfinished agents is reported and exits 0; fewer than half finishing is no review, and exits 2 |

## GitHub Actions

Drop in [`examples/gatehouse.yml`](examples/gatehouse.yml) to review every PR (forks included) via the reusable `review.yml` workflow — the diff is piped as data, never checked out or executed.

The example also runs `Gatehouse triage`, a deterministic check that every required inline finding has an answer it can verify: a reply that starts `fixed in <sha>` naming a commit on the PR, `not a bug: <reason>` with a reason of at least 20 characters, or `deferred: <issue URL>` naming an open issue. Any other reply answers nothing. Findings without a file and line appear only in the review summary and are not counted. CRITICAL, HIGH and MEDIUM findings are required from every agent; LOW findings only from Bug Hunter and Security Scan, and the `required_low_agents` input changes that list (pass the same value to the review and triage jobs, so the review never caps a LOW that triage requires). Other LOWs still post, as advisory.

On a push to an open PR, only the commits since the last complete Gatehouse review are reviewed (`--incremental`), fetched through the compare API. GitHub refuses the diff of a PR of more than 300 files; the workflow then rebuilds it from the PR's file list (up to 3000 files; files with no patch, such as binaries, are skipped with a warning). A PR's first review, a force-push or rebase, a review that had an unfinished agent, or a failed compare all fall back to reviewing the whole PR. The review summary says which it was, and says when a fallback model served any agent; the job's step summary shows per-agent tokens and cost.

Before posting, gatehouse drops four kinds of finding, and the review summary counts each: a finding whose quoted evidence appears nowhere in the diff (or the styleguide and constitution the agents were given), a finding on a line outside the PR diff (GitHub would reject the whole review), a MEDIUM or LOW that repeats an answered thread by the same agent within 5 lines, and advisory LOWs beyond the 5 most confident. Every finding comment carries a hidden `<!-- gatehouse agent=… confidence=… -->` marker for tuning.

A workflow or action under the account that owns the repository is first-party: its owner owns the repository's secrets too, and only that account can move its tags. The agents are told the owner (from `GATEHOUSE_CONTEXT_REPO`, `GITHUB_REPOSITORY` or the `origin` remote) and do not flag the ref a first-party workflow is called at, `@v0` included, or the secrets passed to it. Third-party workflows are reviewed as before, and a call moved from a first-party owner to another one is still flagged.

A release pull request Ashigaru opens (branch `ashigaru/release-*` in the same repository, author `ashigaru-crunchtools[bot]`) is not reviewed: it only moves the changelog and the version strings, which the constitution validator checks. The review job posts nothing and passes, so `Gatehouse triage` passes too.

Add [`examples/gatehouse-retriage.yml`](examples/gatehouse-retriage.yml) as well if `Gatehouse triage` is a required check. A reply to a finding starts a run whose checks branch rules ignore; the retriage workflow re-runs triage inside the `pull_request_target` run, where the result counts.

The review is **advisory by default**: findings post as PR comments and never fail the check, so a non-deterministic LLM finding can never block a merge. An agent that could not finish is named in the review, with the class of failure (`HTTP 402 (payment required)`, `timeout`, ...). When fewer than half of the agents finish, or the reviewer cannot run at all, nothing was reviewed: the review says so instead of "found no issues", the check fails, and `Gatehouse triage` fails with it until the review job is re-run. Do not mark it a required status check. To let critical/high findings fail the check (still not recommended as a required gate), opt in:

```yaml
uses: crunchtools/gatehouse/.github/workflows/review.yml@v0
with:
  blocking: true
```

## Versions

Call the reusable workflows at the major tag, `@v0`, as the examples do. Each release moves that tag once its container images are pushed, so a release reaches every repository without a pin bump there; `review.yml` at the tag names the image of the same release. Every release also keeps its own `vX.Y.Z` tag. To undo a release, move the major tag back: `git tag -f v0 vX.Y.Z && git push -f origin v0`.

A change that needs an edit in the calling repository (an input renamed or removed, a check name changed, a config format Gourmand rejects) is a new major tag, while the version is 0.x as well. Repositories move to a new major by editing their caller files.

To release: bump the version in `pyproject.toml`, `src/gatehouse/__init__.py`, `uv.lock` and the default image in `review.yml`, add the CHANGELOG entry, merge, push the tag `vX.Y.Z`, and publish the GitHub Release (which publishes to PyPI). The tag builds the images and then moves `v0`.

## The finding ledger

A review that raises the same thing every run is a review nobody reads. `gatehouse-findings.toml`, committed at the repository root, records the findings you have already judged, and they are not raised again.

```toml
[[findings]]
agent = "bugs"
category = "bug"
file = "src/app.py"
evidence = "total = total / count"
status = "not-a-bug"
reason = "count is validated non-zero by the only caller, see the guard above"
```

Gatehouse never writes this file — the pre-commit hook mounts your repository read-only, and a reviewer holding a diff it did not write has no business writing to your tree. When it reports a finding it prints the entry ready to paste; you add the reason and commit it.

- `status` is `not-a-bug` or `deferred`, and a deferred entry's reason must link the issue tracking it. A reason must reach 20 characters, the same bar the triage check sets for a reply.
- There is **no `fixed`**. An entry applies while the code it quotes is still there; once that code is gone the entry is stale, and gatehouse prints it so you can delete it. A suppression cannot outlive the lines it covered and go on hiding the next defect in the same place.
- An entry matches a finding by agent, category, file and quoted evidence, by containment — so a judgement survives the next run quoting a little more or less of the same code. A finding with nothing quotable can never be matched, so it can never be silenced this way.
- An entry **added on a branch** is posted on its pull request as a finding a maintainer has to answer: silencing the gate is not a quiet change. Entries already on the base branch were answered when they merged.
- In CI the ledger is read from the pull request's head, since it describes that branch's own code — except from a fork, where the head copy is written by someone who does not own the repository. A fork's judgements are read from the base and take effect once a maintainer merges them.
- A malformed entry is named on stderr and ignored. A ledger can only ever make a review say less, so a typo in one must not stop the review that would have caught something.

## Configuration

Set `OPENROUTER_API_KEY`, or `OPENROUTER_API_KEY_FILE` pointing at a file that holds the key (the file wins when both are set). Either can live in `~/.config/mcp-env/gatehouse.env`. If `.gemini/styleguide.md` exists in the reviewed project, it is injected as context.

### Consensus votes

The sample counts and thresholds live in `gatehouse.toml` at the reviewed repository's root. Every key is optional and overlays the shipped defaults:

```toml
[consensus]
samples = 3          # how many times each agent reviews the diff
block = "all"        # votes a critical/high from a blocking agent needs to fail the run
report = "majority"  # votes any finding needs to be shown

[consensus.agents]
tests = 1            # per-agent overrides, by slug
consistency = 1
general = 1
```

`block` and `report` take `"all"`, `"majority"` (strictly more than half) or an integer, and resolve against each agent's own sample count: at 3 samples `"majority"` is 2, at 5 it is 3. An integer an agent could never reach would silence it, so that is a usage error (exit 2), not a clamp — the message names the agent. An advisory agent resolves no blocking threshold, since it cannot block what it finds.

`--samples N` overrides the count for every agent, then `GATEHOUSE_SAMPLES`, then the file. `--samples 1` restores the single-sample gate, with one deliberate difference from 0.20.0: a critical or high finding that quotes nothing still does not fail the run. A count must be 1-5 and a review may make at most 40 model calls; both are checked before any model is called.

In CI the file is read from the **base** branch, like `.gatehouse-ignore` and the styleguide: a pull request cannot widen the gate that reviews it.

### Ignoring files

List paths that should never be reviewed in `.gatehouse-ignore` at the repo root, in gitignore syntax (`*`, `**`, trailing `/`, `!` negation). Their diff sections and file-listing entries are dropped before any agent sees them, so data files, fixtures, lockfiles and generated output stop costing tokens. A change is skipped only when every path it touches is ignored, so a rename out of an ignored directory is still reviewed, and so is any change to `.gatehouse-ignore` itself.

```gitignore
uv.lock
tests/fixtures/
*.fp
```

In CI the file is read from the base branch, like the styleguide and constitution: a pull request cannot widen it to hide its own changes. The posted review says how many files were skipped; when every changed file is ignored, gatehouse prints `No changes to review.`, exits 0 and posts nothing.

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
