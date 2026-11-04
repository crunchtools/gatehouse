# Spec 003: Consensus Voting and the Finding Ledger

> **Status:** Draft
> **Version:** 0.1.0
> **Author:** Scott McCarty
> **Date:** 2026-11-03
> **Issue:** [#104](https://github.com/crunchtools/gatehouse/issues/104)

## Overview

Gatehouse raises a different set of findings on nearly every run of the same code, so a commit
or a pull request takes three to five review loops instead of one. Measured: three runs on one
trentina PR produced three different sets of HIGH findings, all verified false positives; another
PR took 6-7 rounds (5, 5, 4, 3, 3, 2, 2 findings). The fleet-wide workaround is
`SKIP=gatehouse`, so the local gate is effectively off.

Two causes are tangled together. **Sampling churn:** one LLM call per agent at temperature 0.2,
with no agreement requirement, so a finding one sample invents can block a commit and a finding
one sample misses reappears next run as "new". **No memory:** nothing records that a finding was
already judged — `_drop_reraised` needs an answered GitHub thread, so it does nothing locally.

This spec fixes the first with agreement voting and the second with a committed ledger of judged
findings, and makes the vote counts configurable. It ships in three releases: 0.21.0 consensus
and config, 0.22.0 the ledger and the measurement.

## Non-goals

- **Determinism in the literal sense.** Temperature 0 does not give byte-identical model output
  (provider routing, floating-point non-determinism), so this spec does not promise it.
- **A response cache.** It needs a writable mount, which is a change to the pre-commit hook line
  that 57 repositories pin, hence a fleet-wide major tag — for a property consensus and the
  ledger already give in practice.
- **Wider review scope.** Pre-commit keeps reviewing the staged diff; CI keeps reviewing the PR.
- **Removing the confidence threshold or the advisory LOW cap.** Neither causes the churn.

## User Stories

1. As a developer committing, I want a finding to need agreement from more than one sample before
   it blocks my commit, so a single hallucinated HIGH cannot stop me.
2. As a developer who has judged a finding a false positive, I want it to stay judged, so the next
   run does not raise it again and the local gate is worth leaving installed.
3. As a maintainer, I want every suppression to be visible in the pull request diff and to require
   an answer, so a ledger cannot quietly hide a real defect.
4. As a repository owner, I want the vote counts and thresholds configurable per repository, so a
   security-sensitive repo can demand more agreement than a documentation repo.
5. As a maintainer reviewing a fork's pull request, I want the fork unable to supply either the
   rules it is reviewed by or the suppressions applied to it.

## The contract

"Run once, find everything, fix them all, pass next run" is not achievable for an LLM gate: a fix
legitimately creates new surface. Nor is "the same findings every run": the measurement below
shows the reported set churning, and a promise our own eval contradicts is worse than a narrow
one. What Gatehouse promises is only what it can defend:

- **Same tree, same pass-or-fail verdict.** A tree that fails must not pass on the next run, and
  a tree that passes must not fail. This is the promise, because it is the one that costs a build
  when it breaks.
- **A judged finding never comes back** while the code it quotes is still present. Deterministic:
  no sampling is involved.
- **Only a unanimous critical/high can fail a run**, from a blocking agent. One dissenting sample
  never holds a commit.
- **An honest new defect created by a fix is still raised**, in CI, where `Gatehouse triage` owns
  it and no commit is held hostage.

What is **measured and not promised**: which findings reach unanimity, and the reported set as a
whole. Both are tracked with floors (see Measurement) and watched weekly. A number that moves
with model sampling is a thermometer, not a contract.

## Behavior

### Consensus (0.21.0)

Each agent runs `n` samples concurrently at the existing temperature. Every sample's findings are
unioned for recall, clustered, then tallied additively — no cross-penalties, no weighting, no
synthesizing model.

- votes >= `block`, severity critical or high, and the agent is blocking → blocking (exit 1)
- votes >= `report` → reported, non-blocking locally, answer-required in CI
- fewer votes → shown only under `--verbose`, never persisted

`_drop_unverified` and `_drop_disproved` run per sample, before the tally: a hallucinated quote is
not a vote. The ledger drop, off-diff drop, re-raise drop and advisory LOW cap run after it.

A finding with no usable evidence does not cluster and carries at most one vote, so it can report
from an `n=1` agent and can never block.

Thresholds resolve against the samples that **finished**. An agent counts as unfinished only when
fewer than a majority of its `n` finished. A timeout is not a dissent.

### Clustering

Findings cluster on `(agent slug, category, file)` plus **evidence containment**: two squashed
evidence strings cluster when one contains the other. Not a hash — two samples quoting different
spans of one bug would never cluster and unanimity would never fire. No line numbers take part, so
a cluster survives reformatting and line drift.

### Configuration (0.21.0)

A packaged `defaults.toml` holds the shipped values; an optional `gatehouse.toml` at the reviewed
repository's root overlays it key by key.

```toml
[consensus]
samples = 3
block = "all"
report = "majority"

[consensus.agents]
tests = 1
consistency = 1
general = 1
```

`block` and `report` take `"all"`, `"majority"` (strictly more than half) or an integer, resolved
against that agent's own `n`. An integer above that `n` is unreachable and is a usage error, except `block` for an advisory agent, which resolves no blocking threshold at all.
Precedence: `--samples` > `GATEHOUSE_SAMPLES` > `gatehouse.toml` > packaged defaults; per-agent
counts live in the file only. Bounds: per-agent `n` in [1, 5] and at most 40 calls in one review;
out of range is a usage error, never a silent clamp. `--samples 1` restores single-sample
thresholds, with one deliberate difference from 0.20.0: a critical or high finding that quotes
nothing still does not fail the run.

Under the fork-safe workflow `gatehouse.toml` MUST be loaded from the trusted base, like
`.gatehouse-ignore` and the styleguide: a pull request cannot weaken the gate reviewing it.

### The report override (0.23.0)

`--report-votes N` > `GATEHOUSE_REPORT_VOTES` > `gatehouse.toml` > packaged defaults, overriding
`report` for every agent and leaving `block` untouched. Because it governs only what is shown, and
a cluster with the votes to block is reported whatever `report` says, it cannot weaken the gate in
either direction — so unlike `gatehouse.toml` it is honoured from the invocation even in CI, where
there is no trusted base to read it from.

Where an agent's `n` is below `N` the threshold clamps to that agent's `n`. This is the one
permitted clamp and it is deliberate: the file sets a per-agent count beside the threshold, so an
unreachable value there is a mistake worth exit 2, while a single number on the command line has to
hold for every agent at once, including the three advisory agents that run one sample.

`--report-votes 1` reports every finding one sample saw, with its vote count.

**Known deviation from issue #103.** #103 required that reporting never need a quorum: "a
single-sample finding is still printed, as advisory, with its vote count shown", because the real
CRITICAL that motivated the issue was seen by one run in four. The shipped default
(`report = "majority"`) drops it instead, and a dropped finding is absent from the terminal, the
posted review and `--consensus-json` alike. 0.23.0 makes that recall recoverable on demand; it does
not change the default, because how noisy the gate is out of the box is a product judgement about
adoption, not a correctness one. Open for a ruling.

### The ledger (0.22.0)

`gatehouse-findings.toml`, committed at the reviewed repository's root, records findings already
judged. Statuses are `not-a-bug` and `deferred` only; a fixed finding is one whose quoted code is
gone.

Gatehouse **never writes it** — the pre-commit hook mounts the repository read-only. When a run
has findings it prints ready-to-paste entries on stdout for the developer or agent to fill in and
stage.

An entry suppresses its finding while the evidence it quotes still appears in what the agents were
shown; once that code is gone the entry is stale and is printed for deletion. So a suppression
dies with the lines it quotes and nothing else.

The ledger is read from the pull request **head**, since its entries describe that PR's own code —
except for a fork pull request, where the head copy is attacker-controlled and the base copy is
used instead. Every entry added in a PR is posted as a MEDIUM finding requiring a triage reply, carried by a
`Ledger` pseudo-agent that reviews nothing and cannot block. The existing re-raise window
(same agent, same file, within five lines of an answered thread) dedupes it once answered, so no
separate fingerprint is kept.

### Measurement (0.22.0)

`--consensus-json` writes per-finding votes, thresholds and verdict. `scripts/stability_eval.py`
reviews each diff in a corpus several times and reports three numbers, in order of what they are
worth:

1. **verdict** — did every run agree on pass-or-fail. This is the promise, and it MUST hold for
   every diff in the corpus.
2. **blocking set** — Jaccard over the findings that failed the run. Floor 0.50. Measured 0.56
   and 1.00 on two rounds over the same corpus, so it wobbles and is not promised.
3. **reported set** — Jaccard over everything shown, including the majority tier. Floor 0.20.
   Measured 0.25 to 0.40. This tier is the agreement boundary itself, so it churns by
   construction.

A weekly workflow runs it and prints the table. It is never a required status check: a
live-model eval cannot gate a merge, and a floor is a warning to investigate, not a verdict.

## Scope

- New `src/gatehouse/config.py`, `src/gatehouse/defaults.toml`, `src/gatehouse/ledger.py`
- `review.py`: n-sample fan-out, clustering, tally, filter reorder, unfinished-agent rule
- `cli.py`: `--samples`, `--report-votes`, `--consensus-json`
- `output.py`, `github.py`: vote counts, and `votes=` in the hidden comment marker
- `.github/workflows/review.yml`: pass the head repo and sha for the ledger fetch
- No change to the pre-commit hook line, so no calling repository needs an edit

## Acceptance

1. Three runs on an unchanged tree agree on pass-or-fail for every diff in the corpus, and the
   blocking and reported sets stay above their floors (0.50 and 0.20). Consensus beats
   `--samples 1` on the same corpus.
2. Only a unanimous critical/high from a blocking agent exits 1.
3. `--samples 1` restores single-sample thresholds.
4. An unreachable threshold or an out-of-range sample count exits 2.
5. A ledger entry suppresses its finding while its quote is present, and is reported stale once
   the quoted code is gone.
6. A fork pull request's head `gatehouse.toml` and `gatehouse-findings.toml` are both ignored.
7. The pre-commit hook line is byte-identical to 0.20.0's.
