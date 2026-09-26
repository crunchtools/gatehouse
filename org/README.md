# Org-level configuration

`ruleset-gatehouse-gates.json` is the crunchtools org ruleset (id 23993173,
applied 2026-09-25, RT #1507). It requires the check contexts `Protect workflows` and
`Gatehouse triage / Gatehouse triage` (the reusable-workflow job name as
GitHub reports it) on every repo's default branch. `Gatehouse review` is
advisory and deliberately not required (constitution XII). Org admins bypass,
which keeps direct pushes to default branches working.

Only the `pull_request_target` run's checks count toward these rules; a run
started by a reply to a finding does not. Every repo that carries the
Gatehouse workflow needs `examples/gatehouse-retriage.yml` too, or a reply
leaves triage red until someone re-runs it by hand.

Apply changes with:

    gh api -X PUT orgs/crunchtools/rulesets/23993173 --input org/ruleset-gatehouse-gates.json
