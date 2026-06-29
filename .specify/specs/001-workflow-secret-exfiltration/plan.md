# Plan: 001-workflow-secret-exfiltration

> **Status:** Planning
> **Spec:** [001-workflow-secret-exfiltration](spec.md)

## Summary

Add workflow-file-aware secret exfiltration detection to the Security Scan agent's system prompt. This is a prompt-only change — no code logic changes needed.

## Files to Modify

1. **`src/gatehouse/agents.py`** — Add a workflow-specific detection block to `SECURITY_SCAN.system_prompt`
2. **`tests/test_agents.py`** — Add test verifying the security agent prompt contains workflow exfiltration keywords

## Implementation Steps

1. Add a `WORKFLOW_SECURITY` constant with the workflow-specific detection instructions
2. Append it to the Security Scan agent's system prompt
3. Add a test that asserts the security agent prompt mentions key workflow exfiltration terms
4. Run quality gates (ruff, mypy, pytest)

## Testing Strategy

- Existing tests verify agent structure (slugs, blocking, prompt contents)
- Add test asserting `SECURITY_SCAN.system_prompt` contains workflow-specific keywords (`secrets`, `pull_request_target`, `exfiltration`)
- All Gemini API calls are mocked — no live API calls in tests
