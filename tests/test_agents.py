"""Tests for agent prompt definitions."""

from __future__ import annotations

import pytest

from gatehouse.agents import (
    AGENT_BY_SLUG,
    ALL_AGENTS,
    build_constitution_prompt,
    build_user_prompt,
    get_agents,
)


def test_eight_agents_defined() -> None:
    assert len(ALL_AGENTS) == 8


def test_agent_slugs_unique() -> None:
    slugs = [a.slug for a in ALL_AGENTS]
    assert len(slugs) == len(set(slugs))


def test_all_agents_have_required_fields() -> None:
    for agent in ALL_AGENTS:
        assert agent.name
        assert agent.slug
        assert agent.system_prompt
        assert isinstance(agent.blocking, bool)


def test_blocking_agents() -> None:
    blocking = [a for a in ALL_AGENTS if a.blocking]
    advisory = [a for a in ALL_AGENTS if not a.blocking]
    assert len(blocking) == 5
    assert len(advisory) == 3


def test_blocking_agent_slugs() -> None:
    blocking_slugs = {a.slug for a in ALL_AGENTS if a.blocking}
    assert blocking_slugs == {
        "bugs", "security", "performance", "docs", "constitution",
    }


def test_advisory_agent_slugs() -> None:
    advisory_slugs = {a.slug for a in ALL_AGENTS if not a.blocking}
    assert advisory_slugs == {"tests", "consistency", "general"}


def test_get_agents_all() -> None:
    agents = get_agents(None)
    assert len(agents) == 8


def test_get_agents_filtered() -> None:
    agents = get_agents(["bugs", "security"])
    assert len(agents) == 2
    assert agents[0].slug == "bugs"
    assert agents[1].slug == "security"


def test_get_agents_single() -> None:
    agents = get_agents(["performance"])
    assert len(agents) == 1
    assert agents[0].slug == "performance"


def test_get_agents_invalid() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        get_agents(["nonexistent"])


def test_build_user_prompt_minimal() -> None:
    prompt = build_user_prompt("diff content", None, None)
    assert "diff content" in prompt
    assert "Git Diff" in prompt


def test_build_user_prompt_no_styleguide_section() -> None:
    prompt = build_user_prompt("diff", None, None)
    assert "Styleguide" not in prompt


def test_build_user_prompt_with_styleguide() -> None:
    prompt = build_user_prompt("diff", "my style rules", None)
    assert "my style rules" in prompt
    assert "Styleguide" in prompt


def test_build_user_prompt_with_file_listing() -> None:
    prompt = build_user_prompt("diff", None, "src/main.py\nREADME.md")
    assert "src/main.py" in prompt
    assert "File Listing" in prompt


def test_build_user_prompt_with_all_context() -> None:
    prompt = build_user_prompt("diff", "rules", "files")
    assert "rules" in prompt
    assert "files" in prompt
    assert "diff" in prompt


def test_build_constitution_prompt() -> None:
    prompt = build_constitution_prompt("diff", "rules", None, None)
    assert "rules" in prompt
    assert "Constitution" in prompt
    assert "diff" in prompt


def test_build_constitution_prompt_with_context() -> None:
    prompt = build_constitution_prompt("diff", "rules", "style", "files")
    assert "Constitution" in prompt
    assert "rules" in prompt
    assert "style" in prompt
    assert "files" in prompt
    assert "diff" in prompt


def test_agent_prompts_contain_anti_noise() -> None:
    for agent in ALL_AGENTS:
        assert "Do NOT flag" in agent.system_prompt


def test_anti_noise_includes_diff_direction_awareness() -> None:
    for agent in ALL_AGENTS:
        assert "Fixes being applied" in agent.system_prompt


def test_agent_prompts_contain_output_schema() -> None:
    for agent in ALL_AGENTS:
        assert "JSON array" in agent.system_prompt
        assert "confidence" in agent.system_prompt


def test_agent_prompts_contain_scope() -> None:
    for agent in ALL_AGENTS:
        assert "SCOPE" in agent.system_prompt


def test_security_agent_detects_workflow_exfiltration() -> None:
    prompt = AGENT_BY_SLUG["security"].system_prompt
    assert "workflows" in prompt
    assert "secrets" in prompt
    assert "exfiltration" in prompt
    assert "pull_request_target" in prompt
    assert "CRITICAL" in prompt


def test_agent_by_slug_lookup() -> None:
    assert AGENT_BY_SLUG["bugs"].name == "Bug Hunter"
    assert AGENT_BY_SLUG["security"].name == "Security Scan"
    assert AGENT_BY_SLUG["performance"].name == "Performance Check"
    assert AGENT_BY_SLUG["consistency"].name == "Consistency Check"
    assert AGENT_BY_SLUG["tests"].name == "Test Coverage"
    assert AGENT_BY_SLUG["docs"].name == "Documentation"
    assert AGENT_BY_SLUG["constitution"].name == "Constitution"
    assert AGENT_BY_SLUG["general"].name == "General Review"
