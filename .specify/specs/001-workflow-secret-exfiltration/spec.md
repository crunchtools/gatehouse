# Spec 001: Detect Secret Exfiltration in GitHub Actions Workflows

> **Status:** Draft
> **Version:** 0.1.0
> **Author:** Scott McCarty
> **Date:** 2026-06-29
> **Issue:** [#4](https://github.com/crunchtools/gatehouse/issues/4)

## Overview

Extend the Security Scan agent's system prompt to detect secret exfiltration patterns in GitHub Actions workflow files (`.github/workflows/*.yml`). A malicious PR can sneak in a workflow step that sends `${{ secrets.* }}` to an external URL — this uses GitHub's legitimate secrets syntax, so it doesn't look like a hardcoded credential. The danger is *where* the secret is sent, not how it's stored.

## User Stories

1. As a developer running Gatehouse on a PR diff that modifies `.github/workflows/*.yml`, I want the security agent to flag steps that send secrets to external URLs so I can catch supply-chain attacks before merge.

2. As a project maintainer reviewing open-source contributions, I want Gatehouse to detect the `pull_request_target` + fork checkout anti-pattern so I don't accidentally give fork code access to my repository secrets.

3. As a security-conscious team lead, I want Gatehouse to flag environment variable dumping in workflows so exfiltration via `env`/`printenv` piped to external services is caught.

## Detection Patterns

When the diff includes `.github/workflows/*.yml` files, flag:

1. **Secret exfiltration** — any step that sends `${{ secrets.* }}` to an external URL (curl, wget, httpx, fetch, etc.)
2. **New secret references** — workflow adds `${{ secrets.NEW_KEY }}` not present in the base version
3. **Environment variable dumping** — steps that run `env`, `printenv`, or `set` and pipe/redirect output
4. **Outbound data with secret access** — any step combining secret access with network egress (curl, wget, nc, ssh, scp)
5. **`pull_request_target` with fork checkout** — `pull_request_target` trigger combined with checkout of `github.event.pull_request.head.ref`

## Severity

All workflow secret exfiltration findings should be flagged as **critical** — they enable credential theft after merge.

## Scope

Prompt-only change to the Security Scan agent in `src/gatehouse/agents.py`. No architectural or behavioral changes to the agent framework.
