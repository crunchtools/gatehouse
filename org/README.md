# Org-level configuration

`ruleset-gatehouse-gates.json` is the crunchtools org ruleset (id 23993173,
applied 2026-09-25, RT #1507). It requires `Protect workflows` and
`Gatehouse triage` on every repo's default branch. `Gatehouse review` is
advisory and deliberately not required (constitution XII). Org admins bypass,
which keeps direct pushes to default branches working.

Apply changes with:

    gh api -X PUT orgs/crunchtools/rulesets/23993173 --input org/ruleset-gatehouse-gates.json
