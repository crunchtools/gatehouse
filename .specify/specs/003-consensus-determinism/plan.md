# Plan: 003-consensus-determinism

> **Status:** Planning
> **Spec:** [003-consensus-determinism](spec.md)

## Summary

Three releases on one branch. 0.21.0 adds configurable consensus voting: each agent runs `n`
samples, findings cluster by evidence containment, votes tally additively, and only a unanimous
critical/high from a blocking agent blocks. 0.22.0 adds the committed, read-only
`gatehouse-findings.toml` ledger, whose entries expire with the code they quote. 0.23.0 adds the
JSON dump and the stability eval. The pre-commit hook line never changes, so no calling repository
needs an edit.

Design ruled by the `cto` agent (Fable) on 2026-11-03 and red-teamed against the written plan;
the approved plan lives at `~/.claude/plans/eager-crafting-raven.md`.

## Files to Create

1. **`src/gatehouse/config.py`** — overlay `gatehouse.toml` on the packaged defaults, resolve
   `block`/`report` per agent against that agent's `n`, validate bounds, exit 2 on a bad config
2. **`src/gatehouse/defaults.toml`** — the shipped values, packaged by hatchling
3. **`src/gatehouse/ledger.py`** — parse `gatehouse-findings.toml`, containment matching,
   staleness via the existing evidence check, render paste-ready and stale entries
4. **`scripts/stability_eval.py`** — three runs over archived diffs, cluster-set Jaccard
5. **`tests/test_config.py`**, **`tests/test_consensus.py`**, **`tests/test_ledger.py`**,
   **`tests/test_consensus_json.py`**
6. **`gatehouse.toml`**, **`gatehouse-findings.toml`** — this repo dogfooding its own gate

## Files to Modify

1. **`src/gatehouse/review.py`** — `_run_agents` fans out `n` samples per agent; new `_cluster`
   and `_tally`; `_filter_findings` split around the tally; `_has_blocking_findings` consults
   votes; the unfinished-agent rule counts finished samples; `MAX_CONCURRENT_CALLS` 5 → 24
2. **`src/gatehouse/cli.py`** — `--samples`, `GATEHOUSE_SAMPLES`, `--consensus-json`
3. **`src/gatehouse/output.py`** — print `votes=k/n` per finding
4. **`src/gatehouse/github.py`** — `votes=` and `fp=` in the hidden marker; post new ledger
   entries as answerable findings
5. **`.github/workflows/review.yml`** — pass head repo and sha for the ledger fetch
6. **`.specify/memory/constitution.md`**, **`README.md`**, **`CLAUDE.md`**, **`CHANGELOG.md`**

## Reused, not rebuilt

- `_squash` (`review.py:302`) for containment matching
- `_evidence_is_real` (`review.py:317`) for ledger staleness
- `_load_context_file` (`review.py:192`) so `gatehouse.toml` comes from the trusted base
- `fetch_repo_file` (`github.py:54`) for the head-side ledger fetch
- `_keep_where` (`review.py:338`) for the ledger drop, like every other filter

## Implementation Steps

**0.21.0 — consensus**

1. `C1` this spec and the constitution amendment
2. `C2` `config.py` and `defaults.toml` with their tests
3. `C3` n-sample fan-out, partial-sample rule, semaphore to 24
4. `C4` clustering by containment, additive tally, evidence-less cap, filter reorder
5. `C5` vote counts in terminal output and in the comment marker
6. `C6` `--samples`, `GATEHOUSE_SAMPLES`, precedence, docs, version bump

**0.22.0 — ledger**

7. `L1` `ledger.py`: parse, containment match, staleness
8. `L2` wire the ledger drop after the tally; print paste-ready and stale entries
9. `L3` config from base, ledger from head, fork reads base only
10. `L4` new entries post as answerable findings; `fp=` in the marker
11. `L5` dogfood both files here; the ledger's constitution text; docs, version bump

**0.23.0 — measurement**

12. `M1` `--consensus-json`
13. `M2` the stability eval over archived diffs
14. `M3` docs, version bump

## Testing

Mocked OpenRouter, as today. Tally arithmetic at n=1..5 against each threshold form; an
unreachable threshold and an out-of-range count both exit 2; containment clustering (a one-line
and a three-line quote of one bug cluster, two unrelated findings in one file do not); an
evidence-less finding capped at one vote; a ledger entry that suppresses while its quote is
present and is stale once it is gone; the partial-sample rule (1 of 3 finished is unfinished, 2 of
3 is a valid 2/3 tally); `gatehouse.toml` read from the base under `GATEHOUSE_CONTEXT_REPO`/`REF`;
a fork PR ignoring a head ledger, in `tests/test_fork_safety.py`.

Live checks before each release: two runs on an unchanged tree report the same clusters; latency
p90 at or under about 60s for the default 18 calls, from `--usage-json`; the unchanged hook line
still works from trentina and rotv.

## Rollout

48 repositories call the reusable workflows at `@v0` and pick each release up when its tag moves.
Nine pin exact tags and need `scripts/fleet-bump.py` in crunchtools/constitution, run after
0.22.0 so they land on consensus and the ledger together.
