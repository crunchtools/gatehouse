# Org-level configuration

`ruleset-gatehouse-gates.json` is the crunchtools org ruleset (id 23993173,
applied 2026-09-25, RT #1507). It requires the check contexts `Protect workflows`,
`Gatehouse triage / Gatehouse triage`, `Code Quality (Gourmand) / Code Quality
(Gourmand)` and `Constitution / Validate constitution` (reusable-workflow job
names as GitHub reports them) on every repo's default branch. `Gatehouse
review` is advisory and deliberately not required (constitution XII).

Nobody bypasses. Agents act through an org-admin token, so an admin bypass let
any agent push straight to a default branch, past every gate -- including
edits to the exception files the gates read. Every change, fleet rollouts
included, goes through a PR. Break-glass is to add a bypass actor by API,
make the change, and remove it again.

Only the `pull_request_target` run's checks count toward these rules; a run
started by a reply to a finding does not. Every repo that carries the
Gatehouse workflow needs `examples/gatehouse-retriage.yml` too, or a reply
leaves triage red until someone re-runs it by hand.

Apply changes with:

    gh api -X PUT orgs/crunchtools/rulesets/23993173 --input org/ruleset-gatehouse-gates.json
