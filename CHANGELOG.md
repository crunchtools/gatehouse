# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and Semantic Versioning.

## [Unreleased]

## [0.23.0] - 2026-11-04

### Added

- **`--report-votes N`** (and `GATEHOUSE_REPORT_VOTES`) overrides how many
  votes a finding needs to be *shown*. `--report-votes 1` reports every
  finding a single sample saw, with its `1/3` beside it (#103).
- It cannot change a verdict in either direction: a finding with the votes to
  block is printed whatever the report threshold says. That is why, unlike
  `gatehouse.toml`, it is honoured from the command line in CI instead of
  being read from the base branch — there is no gate to weaken.
- Where an agent runs fewer than `N` samples the threshold clamps to that
  agent's own count. The config file still refuses an unreachable threshold,
  because there you set a per-agent count beside it; one number passed on the
  command line has to hold for every agent at once, including the three
  advisory ones that run a single sample.

### Fixed

- **`--verbose` now reports the findings below the report threshold**, which
  the README, 0.21.0's release notes and this project's own constitution all
  said it already did. It did not: a below-threshold finding was dropped from
  the terminal, the posted review and `--consensus-json` alike, and
  `--verbose` only dumped each agent's raw unparsed reply. Found by Gatehouse
  reviewing this diff against the constitution it ships with.

### Known trade-off

0.21.0 made the default report threshold a majority, which means the shipped
defaults drop a finding only one sample in three saw. Issue #103, which asked
for consensus voting, explicitly required the opposite: "a single-sample
finding is still printed, as advisory, with its vote count shown", on the
strength of a real CRITICAL that one run in four caught. The shipped default
contradicts that requirement. Changing the default is a judgement about how
noisy the gate should be out of the box, which is why this release makes the
recall recoverable — by flag, and under `--verbose` — rather than flipping the
default. Open for a product ruling.

### Also in this release

- A release of this image now **triggers a python-sdlc rebuild**
  (`.github/workflows/container.yml`). python-sdlc bakes this image's venv, so
  without the trigger a release here left every Python repo's local review on
  the previous Gatehouse while CI ran this one. Found by
  `crunchtools/constitution`'s `validate-cascade` check.

## [0.22.0] - 2026-11-03

### Added

- **The finding ledger.** `gatehouse-findings.toml`, committed at the reviewed
  repository's root, records findings already judged (`not-a-bug` or
  `deferred`) and they are not raised again. Gatehouse never writes it — the
  pre-commit hook mounts the repository read-only — so it prints each entry
  ready to paste and the author fills in the reason and commits it (#104).
- An entry applies while the code it quotes is still present, matched by
  containment so a judgement survives the next run quoting a little more or
  less of it; once that code is gone the entry is stale and is printed for
  deletion. There is no `fixed` status, and a suppression cannot outlive the
  lines it covered.
- An entry added on a branch is posted on its pull request as a MEDIUM finding
  a maintainer has to answer, so silencing the gate is never a quiet change.
  The existing re-raise window dedupes it once answered.
- In CI the ledger is read from the pull request's head, since it describes
  that branch's own code — except from a fork, whose head copy is read from the
  base instead: a fork must not be able to suppress its own findings.
  `review.yml` passes `GATEHOUSE_HEAD_REPO` to tell the two apart.
- A malformed ledger is named on stderr and ignored, never fatal.
- `--consensus-json PATH` writes one record per reported finding: its identity
  (agent, category, file and squashed quote), votes, blocking votes, the
  thresholds it was judged against and whether it blocked.
- `scripts/stability_eval.py` reviews each diff in `tests/corpus` several times
  and reports the finding-set Jaccard index, with `--samples 1` for the
  pre-consensus baseline. A weekly `Stability` workflow runs it into the step
  summary; it is never a required check.

### Measured

Three diffs, three runs each, including one carrying planted defects:

- **Pass-or-fail verdict: agreed every time**, on every diff, in both rounds.
  This is what the spec now promises, because a tree that passes once and
  fails the next time is what costs a build.
- **Blocking set** (which findings reached unanimity): 0.56 in one round, 1.00
  in the next. It wobbles; floor 0.50, measured, not promised.
- **Reported set** (everything shown, majority tier included): 0.25 to 0.40,
  against **0.22** for the pre-consensus baseline on the same corpus. Floor
  0.20. This tier is the agreement boundary itself, so it churns by
  construction.

Spec 003's contract was narrowed to match: the verdict and the ledger are the
promises, the finding sets are measurements with floors. An earlier draft
promised "same tree, same verdict" on the strength of a corpus in which
nothing ever blocked — empty versus empty scores a perfect 1.00 and proves
nothing. The corpus now carries a diff with real defects in it for that
reason.

## [0.21.0] - 2026-11-03

### Added

- **Consensus voting.** Each agent now reviews the diff `samples` times and the
  samples are tallied: a finding's votes are how many samples saw it. The
  shipped defaults sample the five blocking agents three times and the three
  advisory agents once, so a critical/high from a blocking agent must be
  unanimous (3/3) to fail the run and needs a majority (2/3) to be reported at
  all, while an advisory agent keeps its single-sample behaviour (1/1 to report,
  never blocking). A HIGH only one sample saw is no longer a blocked commit. Findings cluster on
  agent, category, file and evidence containment, so a sample quoting one line
  and a sample quoting the three around it count as one finding; a hash of the
  quote would split them and unanimity would never be reached (spec 003, #104).
- `gatehouse.toml` at the reviewed repository's root configures it: `[consensus]`
  `samples`, `block` and `report`, plus `[consensus.agents]` per-agent sample
  counts. `block` and `report` take `"all"`, `"majority"` or an integer and
  resolve against each agent's own count. In CI the file is read from the base
  branch, like `.gatehouse-ignore`, so a pull request cannot weaken the gate
  reviewing it.
- `--samples N` and `GATEHOUSE_SAMPLES` override the count globally;
  `--samples 1` restores single-sample thresholds. One deliberate difference
  from 0.20.0: a critical or high finding that quotes nothing no longer fails
  the run, because nothing about it can be checked. Per-agent `N` is capped
  at 5 and a review at 40 model calls; an out-of-range count, or a threshold an
  agent could never reach, exits 2 before any model is called.
- Terminal output says ", agreed by 2 of 3 samples" and the hidden PR comment
  marker carries `votes=2/3`.

### Changed

- `MAX_CONCURRENT_CALLS` 5 -> 24, so the default 18 calls run in one wave.
- An agent is unfinished only when fewer than a majority of its samples came
  back: a timeout is not a vote against a finding, and the samples that did
  return still decide. A minority of lost samples is reported on stderr.
- Thresholds resolve against the samples that *finished*, so a lost sample does
  not raise the bar for the ones that came back: two of three samples agreeing
  on a critical finding still fails the run.
- Whether a finding fails the run is decided once, during the tally, where the
  agent's thresholds and its finished sample count are both in hand. A finding
  with the votes to block is always shown, even if it is short of the report
  threshold: a run must never fail on a finding it did not print.
- The per-sample filters (invented evidence, disproved refs) run before the
  tally and the cluster filters (off-diff lines, re-raises, the advisory LOW
  cap) after it, so a hallucinated quote never becomes a vote and a cap applies
  to a finding rather than to each sample's copy of it.

## [0.20.0] - 2026-10-31

### Changed

- Container base images pinned to `quay.io/hummingbird/python:3.14*` instead of
  `latest`, which moved to Python 3.15.0 on 2026-10-20 (crunchtools/constitution#66).
- `review.yml` skips Ashigaru release pull requests: branch `ashigaru/release-*`
  in the same repository, opened by `ashigaru-crunchtools[bot]`. It posts nothing
  and passes, so triage passes. Those PRs only move the changelog and version
  strings, which the constitution validator now checks (v1.28.0), and one
  unanswered finding there held the mcp-metsuke 3.5.0 release for four days.
- The Containerfile `version` label said 0.2.0; it now tracks the release.

### Security

- CVE-2026-114613 (msgpack 1.1.2 vendored in pip) is accepted in `.trivyignore`
  until a pip release vendors msgpack 1.2.3; gatehouse never runs pip.

## [0.19.0] - 2026-10-16

### Changed
- Triage counts a finding as answered only by a verifiable reply:
  `fixed in <sha>` naming a commit on the PR, `not a bug: <reason>` with a
  reason of 20+ characters, or `deferred: <issue URL>` pointing at an open
  issue. Any other reply ("won't fix", "ok") left the thread looking answered;
  now the check stays red. Open PRs answered the old way need a re-reply.
  On a private repo a `deferred:` issue cannot be looked up with the token
  callers grant, so use `fixed in` or `not a bug:` there.

### Fixed
- A finding that calls an action or workflow ref missing (`actions/checkout@v7
  does not exist`) is checked against GitHub, and dropped when every ref it
  cites resolves. The review body counts them. The 0.16.0 prompt rule did not
  stop the finding; a lookup does. A failed lookup keeps the finding.

## [0.18.1] - 2026-10-16

### Fixed

- The org ruleset let org admins bypass it, and agents act as an org admin,
  so an agent could push past every gate straight to a default branch (one
  did, editing Gourmand exceptions). The bypass is gone. The ruleset file
  also listed 2 of the 4 checks the live ruleset requires; it now matches.
- pip-audit's vulnerability count parsed the report as a list, threw on
  pip-audit's dict output, and fell back to 0, so the PR fail step never
  fired. An unreadable report now fails the step.
- The PR container scan ran Trivy from an unpinned `@master` with exit code
  0. It is pinned and fails a PR on fixable HIGH/CRITICAL findings; accepted
  ones go in `.trivyignore` with a reason.
- anyio 4.15.1 and idna 3.20, clearing the advisories the working pip-audit
  gate found.

## [0.18.0] - 2026-10-14

### Added
- A workflow or action owned by the account that owns the repository under
  review is first-party. The agents are told the owner, from
  `GATEHOUSE_CONTEXT_REPO`, `GITHUB_REPOSITORY` or the `origin` remote, and
  no longer flag the ref a first-party workflow is called at (a major tag such
  as `@v0` included) or the secrets passed to it. Workflows from any other
  owner are judged as before, and a call that moves to another owner is still
  a finding. Before, the major-tag pin the constitution requires drew a
  "mutable tag, pin to a SHA" finding on every caller-file change, 378 of the
  759 findings answered "not a bug" from October 1 to 11.

### Changed
- The Test Coverage agent no longer asks for tests of `.github/` workflow files.

## [0.17.0] - 2026-10-11

### Changed
- Repositories call the reusable workflows at the major tag, `@v0`, and no
  longer at an exact release. A release tag builds the images and then moves
  `v0`, so every repository runs the new release with no pin bump. Before,
  each release was re-pinned in every repository by a Dependabot pull request,
  and repositories sat one and two releases behind. Every release keeps its
  `vX.Y.Z` tag; a release is undone by moving `v0` back. A change that needs
  an edit in the calling repository is a new major tag from now on.
- The container build runs for release tags (`v*.*.*`) only, not for `v0`.

## [0.16.0] - 2026-10-10

### Changed
- A review in which fewer than half of the agents finished exits 2 even under
  `--advisory`, and its posted summary reads "could not review this PR: 0 of 8
  agents finished" instead of "found no issues". A review with a clean result
  from only some agents says how many finished (#77).
- `Gatehouse triage` fails while the newest Gatehouse review on the PR is
  marked failed. Re-running the review job clears it (#77).
- The posted review names why an agent could not finish, as a class: `HTTP
  402 (payment required)`, `HTTP 429 (rate limited)`, `timeout`, `connection
  error`, `unparseable reply` (#77).

### Fixed
- The review workflow no longer dies on a PR of more than 300 files, where
  GitHub refuses the diff with HTTP 406. It rebuilds the diff from the PR's
  file list instead (#75).
- When the review workflow cannot run the reviewer at all (no diff, no
  `OPENROUTER_API_KEY`, image will not start), it posts a failed review, so
  triage holds the PR instead of passing on "no findings" (#75).

## [0.15.3] - 2026-10-02

### Changed
- CI: the Gourmand job reports as `Code Quality (Gourmand) / Code Quality
  (Gourmand)` like the rest of the fleet, so the org ruleset can require it.
- Constitution is now a v1.18.0 manifest: purpose, CLI and workflow contract,
  external API, agents and ignored paths; fleet and profile rules apply by
  reference.
- Constitution validation pinned to v1.18.0 via `constitution.yml`, replacing
  the unpinned validation job in `ci.yml`.
- Dependabot auto-merges GitHub Actions minor and patch updates.

### Fixed
- Reviewers no longer flag a version, tag or release as nonexistent. Every
  Dependabot bump to a major newer than the model's cutoff (checkout@v7,
  setup-python@v7, ...) drew a "tag does not exist" finding, repeated on
  each rebase.
- `examples/gourmand.yml` pinned `gourmand.yml@v0.5.0` and was a bare job
  fragment. It is now a complete workflow on `pull_request` at the current
  release, and a test fails any example that pins a different release than
  the one it ships in (crunchtools/constitution#22).

## [0.15.2] - 2026-09-30

### Fixed
- **Replies with trailing content no longer abort an agent.** GLM-5.3 often
  follows its findings array with a second array or prose, and `json.loads`
  rejected the whole reply as `Extra data`, marking the agent unfinished.
  Consecutive JSON values are now merged and trailing text is ignored; a reply
  that does not start with JSON is still an unfinished review.

## [0.15.1] - 2026-09-29

### Fixed
- **Review no longer fails writing the step summary (#61, #62).** Container
  root got `PermissionError` on the bind-mounted `$GITHUB_STEP_SUMMARY`, so a
  finished review exited 1. `review.yml` now gives the container a scratch
  directory and appends the usage table from the host, keeping gatehouse's
  exit code. A failed summary write only warns.

## [0.15.0] - 2026-09-29

### Added
- **Incremental re-review (#57).** On a push to an open PR, `review.yml`
  passes `--incremental`: gatehouse reviews only the commits since its last
  complete review, fetched through the compare API with nothing checked
  out. It falls back to the whole PR for a first review, a force-push or
  rebase, when the last review had an unfinished agent, or when the compare
  fails. Files the PR does not touch are cut from the range, so merging the
  base branch in does not review the base's changes. The review summary
  states the scope.
- **Serving model and usage per agent (#58).** Each agent logs
  `agent=… model=… prompt=… completion=… reasoning=… cost=$… fallback=yes|no`
  to stderr, followed by a total line. In CI the same table goes to the job's
  step summary, and `--usage-json PATH` writes it as JSON.
- **Fallback is visible.** When a fallback model served any agent, the posted
  review says so, e.g. "3 of 8 agents served by google/gemini-3.1-flash-lite".

### Changed
- Findings on a line outside the PR diff are not posted, and the summary
  counts them. GitHub rejects a whole review over one such comment.
- The posted review is pinned to the head commit that was reviewed, and a
  review in which every agent finished carries a hidden
  `<!-- gatehouse review complete -->` marker; incremental review starts
  from the newest one.
- `call_model` returns a `Completion` (text, requested and serving model,
  usage) instead of the bare text.

## [0.14.0] - 2026-09-29

LOW findings were 26% of the threads triage required an answer to, and a
30-finding sample from four crunchtools PRs held 18 noise, 6 re-raises of
answered threads and 1 with invented evidence (#56).

### Added
- **Findings with invented evidence are dropped**, at every severity. The
  quoted evidence must appear in the diff (or the styleguide/constitution
  agents were given), ignoring whitespace, line-number prefixes and `...`
  elisions. mcp-trentina#258 quoted `def _cases(split: str, csv_bytes:
  bytes):` for code that reads `-> list[Case]:`.
- **Answered threads are not re-raised.** On a PR, a MEDIUM or LOW from the
  same agent within 5 lines of a thread someone replied to is dropped.
  HIGH and CRITICAL always post: next to a "fixed in" thread they may be a
  regression.
- **At most 5 advisory LOWs per review**, highest confidence first. LOWs
  from the `--required-low-agents` list (Bug Hunter and Security Scan by
  default) are never capped.
- The review summary counts each kind of dropped finding.
- Each finding comment carries a hidden `agent` and `confidence` marker, so
  replies can be joined to confidence before deciding whether LOW needs a
  higher threshold.
- `required_low_agents` input on `triage.yml` and `review.yml` (default
  `Bug Hunter,Security Scan`), and `--required-low-agents` on the CLI. Give
  both workflows the same list.

### Changed
- **Triage requires an answer to LOW findings only from `required_low_agents`.**
  Other LOWs still post, as advisory. CRITICAL, HIGH and MEDIUM are required
  from every agent, as before.
- The Documentation agent no longer asks for docstrings on private or
  non-exported names, or beyond a file's one-line-docstring-plus-type-hints
  convention; its `low` tier is gone.
- Consistency Check findings must cite the in-repo code that sets the
  convention, as `file:line`, or are not reported.

## [0.13.0] - 2026-09-26

### Changed
- **The Constitution agent reviews amendments as amendments.** A diff that
  changes the constitution file itself is reviewed for rules that contradict
  each other or the code, not against the text it replaces, which it can
  only ever contradict. crunchtools/petit#77 and #78 each drew CRITICAL
  findings for changing the rule they were changing. Other files in the
  same diff are still judged by the base branch's constitution, so a PR
  still cannot relax a rule and use the relaxation at once. The agent is
  also told it does not know today's date, which made it flag same-day
  `Amended:` lines as stale.

## [0.12.0] - 2026-09-26

### Added
- **`.gatehouse-ignore`**: gitignore-syntax paths that are never reviewed.
  Matching files leave the diff and the file listing before any agent runs;
  on crunchtools/petit#61 that cuts each agent's diff from 263 KB of deleted
  fingerprint data to 3 KB. A change is skipped only when every path it
  touches matches, changes to the ignore file itself are always reviewed,
  and in CI the file comes from the base branch so a PR cannot hide its own
  changes. The posted review says how many files were skipped (when every changed
  file is ignored, nothing is posted).
- Under a trusted base (`GATEHOUSE_CONTEXT_REPO`/`REF`), the styleguide,
  ignore file and auto-discovered constitution come only from the base; a
  missing base copy no longer falls back to the working tree. An explicit
  `--constitution` path still wins, as before.

## [0.11.0] - 2026-09-25

### Added
- **`retriage.yml` and `examples/gatehouse-retriage.yml`: a reply now clears
  the triage check.** Triage re-ran on `pull_request_review_comment`, but that
  run's checks do not count toward branch rules: on #50, four passing reply
  runs left the PR blocked until the `pull_request_target` run's triage job
  was re-run by hand. The new `workflow_run` listener does that re-run
  automatically, from the default-branch definition. Repos that require
  `Gatehouse triage` should add the example file.

### Security
- **Answers #47.** Because reply-event checks do not count toward the
  ruleset, a fork that rewrites its copy of the workflow cannot satisfy
  `Protect workflows` or `Gatehouse triage` on a reply. The re-run that does
  count uses the base definition.

## [0.10.0] - 2026-09-25

### Changed
- **An agent that cannot finish now exits 2.** It used to print a warning and
  count as zero findings, so an OpenRouter outage or a garbled reply passed
  as a clean review (the pre-commit hook included). Findings from the agents
  that did finish are still printed and posted, and the posted review names
  the ones that did not. `--advisory`, and so the default `review.yml`, still
  exits 0 (#41).

### Fixed
- **`--stdin` crashed on `httpx.ReadTimeout` over a large diff.** Timeouts and
  dropped connections are now retried with the same backoff as a 429 (#41).
- **`review.yml` failed on diffs containing escape sequences.** `gh pr diff`
  refuses such a diff unless told otherwise, printed nothing, and the job
  reported `No changes to review.` before exiting 1. The diff is now fetched
  with `--allow-escape-sequences` into a file, and a failed fetch says so
  (#44).
- **The workflow guard missed renames.** It read `gh pr diff --name-only`,
  which lists only a renamed file's new path, so a fork could move a workflow
  out of `.github/workflows/` (deleting it) and pass. The guard in
  `examples/gatehouse.yml` and in this repo now reads both `filename` and
  `previous_filename` from the pull-request files API. Found by Gatehouse
  reviewing its own fleet rollout (RT #1507); repos that copied the example
  should re-copy it.
- **A reply could clear a failed workflow guard.** The guard ran only on
  `pull_request_target`, so on a `pull_request_review_comment` run it reported
  `skipped`, and a required check counts `skipped` as passing. It now runs on
  both events, and posts its explanation only once.

## [0.9.0] - 2026-09-24

### Changed
- **Gatehouse calls OpenRouter, not the Gemini API.** The default model is
  `openai/gpt-6-luna`, with `google/gemini-3.1-flash-lite` as an automatic
  fallback when it is rate-limited or down. On a 33-diff replay of real
  crunchtools changes, judged blind by two models from other vendors, Luna
  caught as many reintroduced bugs as `gemini-2.5-flash` while nearly all of
  2.5 Flash's findings on clean PRs were noise, at about a sixth of the cost
  per review (RT #1505).
- Every request requires zero data retention and forbids training on prompts.
- `--model` takes an OpenRouter slug.
- A reply wrapped as `{"findings": [...]}` is unwrapped instead of dropped.

### Added
- `OPENROUTER_API_KEY_FILE`: read the key from a file (wins over the
  variable; warns when the file is group- or world-readable).

### Removed
- **Breaking:** `GEMINI_API_KEY` is no longer read. Replace it with
  `OPENROUTER_API_KEY` in `~/.config/mcp-env/gatehouse.env` and in the
  GitHub secret passed to `review.yml`. gatehouse exits 2 with a pointer
  when only the old key is set.

## [0.8.0] - 2026-09-24

### Added
- **`triage.yml`, a deterministic merge condition for an advisory reviewer.**
  Findings were posted and merged past unanswered: one adopter merged a PR
  with 40 findings (21 high) five minutes after the review, replying to none.
  The new reusable workflow fails while any inline finding lacks a reply by
  someone other than the reviewer. It never judges the code, so unlike the
  review it is safe to mark as a required status check.
- **`examples/pre-commit.yaml`**: the mandatory local gates, Gourmand and a
  Gatehouse hook that reviews the staged diff from the container image and
  blocks the commit on critical/high findings.

### Changed
- `examples/gatehouse.yml` adds the triage job and the
  `pull_request_review_comment` trigger that re-runs it on a reply; guard
  and review are skipped for that event. It also moves to `review.yml@v0.8.0`
  (it still pointed at v0.2.0) and to the head-repo guard this repo already
  uses — the example had kept the `author_association` check that trusted a
  collaborator's fork and failed every Dependabot PR.
- This repo's own `.pre-commit-config.yaml` runs the Gatehouse hook.

## [0.7.1] - 2026-09-23

### Fixed
- **`review.yml` defaults to this release's image.** The default image had
  stayed at `quay.io/crunchtools/gatehouse:0.4.0` since 0.4.0, so callers
  pinning `review.yml@v0.5.0` through `@v0.7.0` still reviewed with the 0.4.0
  container. A new test fails when the default tag and `__version__` disagree.

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
