"""Tests for consensus configuration loading, overlay and threshold resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gatehouse.config import (
    MAX_TOTAL_CALLS,
    AgentConsensus,
    ConfigError,
    ConsensusConfig,
    load_config,
    packaged_defaults,
    resolve_threshold,
    samples_from_env,
)


def test_packaged_defaults_are_three_blocking_one_advisory() -> None:
    config = packaged_defaults()
    assert config.samples == 3
    assert config.samples_for("bugs") == 3
    assert config.samples_for("security") == 3
    assert config.samples_for("docs") == 3
    assert config.samples_for("constitution") == 3
    assert config.samples_for("performance") == 3
    assert config.samples_for("tests") == 1
    assert config.samples_for("consistency") == 1
    assert config.samples_for("general") == 1


def test_default_review_is_eighteen_calls() -> None:
    config = packaged_defaults()
    slugs = [
        "bugs",
        "security",
        "performance",
        "tests",
        "docs",
        "constitution",
        "consistency",
        "general",
    ]
    assert config.total_calls(slugs) == 18
    config.check_budget(slugs)


def test_blocking_agent_needs_unanimity_advisory_cannot_block() -> None:
    config = packaged_defaults()
    bugs = config.for_agent("bugs")
    assert (bugs.samples, bugs.block, bugs.report) == (3, 3, 2)
    general = config.for_agent("general")
    assert (general.samples, general.block, general.report) == (1, None, 1)


def test_integer_block_threshold_ignores_advisory_sample_counts() -> None:
    """An advisory agent at n=1 is not a conflict with a global block = 4."""
    config = load_config("[consensus]\nsamples = 5\nblock = 4\n")
    assert config.for_agent("bugs").block == 4
    assert config.for_agent("tests").block is None


@pytest.mark.parametrize(
    ("samples", "expected_all", "expected_majority"),
    [(1, 1, 1), (2, 2, 2), (3, 3, 2), (4, 4, 3), (5, 5, 3)],
)
def test_threshold_forms_resolve_against_sample_count(
    samples: int, expected_all: int, expected_majority: int
) -> None:
    assert resolve_threshold("all", samples, "block", "bugs") == expected_all
    assert resolve_threshold("majority", samples, "report", "bugs") == expected_majority


def test_integer_threshold_is_itself() -> None:
    assert resolve_threshold(2, 3, "report", "bugs") == 2


def test_unreachable_integer_threshold_is_an_error() -> None:
    with pytest.raises(ConfigError, match="never be reached"):
        resolve_threshold(4, 3, "block", "bugs")


def test_threshold_below_one_is_an_error() -> None:
    with pytest.raises(ConfigError, match="at least 1"):
        resolve_threshold(0, 3, "block", "bugs")


def test_boolean_threshold_is_an_error() -> None:
    with pytest.raises(ConfigError, match="not a boolean"):
        resolve_threshold(True, 3, "block", "bugs")


def test_unknown_threshold_word_is_an_error() -> None:
    with pytest.raises(ConfigError, match="must be"):
        resolve_threshold("most", 3, "block", "bugs")


def test_repo_file_overlays_key_by_key() -> None:
    config = load_config("[consensus]\nsamples = 5\n")
    assert config.samples == 5
    assert config.block == "all"
    assert config.samples_for("tests") == 1  # untouched by the overlay
    assert config.for_agent("bugs").block == 5


def test_repo_file_can_override_one_agent() -> None:
    config = load_config("[consensus.agents]\ntests = 3\n")
    assert config.samples_for("tests") == 3
    assert config.samples_for("bugs") == 3
    assert config.for_agent("tests").report == 2


def test_repo_file_can_set_an_integer_threshold() -> None:
    config = load_config(
        "[consensus]\nsamples = 5\nblock = 4\nreport = 2\n"
        "[consensus.agents]\ntests = 2\nconsistency = 2\ngeneral = 2\n"
    )
    bugs = config.for_agent("bugs")
    assert (bugs.block, bugs.report) == (4, 2)
    assert config.for_agent("tests").report == 2


def test_integer_report_threshold_an_agent_cannot_reach_names_that_agent() -> None:
    """report = 2 would silence a one-sample agent outright, so it fails loudly."""
    with pytest.raises(ConfigError, match="'tests' runs 1 sample"):
        load_config("[consensus]\nsamples = 5\nreport = 2\n")


def test_unreachable_threshold_in_repo_file_fails_before_any_call() -> None:
    with pytest.raises(ConfigError, match="never be reached"):
        load_config("[consensus]\nsamples = 2\nblock = 3\n")


def test_samples_over_the_cap_is_an_error() -> None:
    with pytest.raises(ConfigError, match="between 1 and 5"):
        load_config("[consensus]\nsamples = 50\n")


def test_samples_below_one_is_an_error() -> None:
    with pytest.raises(ConfigError, match="between 1 and 5"):
        load_config("[consensus]\nsamples = 0\n")


def test_unknown_agent_slug_is_an_error() -> None:
    with pytest.raises(ConfigError, match="unknown agent"):
        load_config("[consensus.agents]\nbuggs = 2\n")


def test_malformed_toml_is_an_error() -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config("[consensus\nsamples = 3\n")


def test_non_integer_samples_is_an_error() -> None:
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config('[consensus]\nsamples = "three"\n')


def test_consensus_must_be_a_table() -> None:
    with pytest.raises(ConfigError, match="must be a table"):
        load_config("consensus = 3\n")


def test_samples_override_wins_and_clears_per_agent_counts() -> None:
    config = load_config(
        "[consensus]\nsamples = 5\n[consensus.agents]\ntests = 3\n", samples_override=1
    )
    assert config.samples == 1
    assert config.samples_for("tests") == 1
    assert config.samples_for("bugs") == 1


def test_samples_one_reproduces_single_sample_behaviour() -> None:
    config = load_config(None, samples_override=1)
    for slug in ("bugs", "security", "performance", "docs", "constitution"):
        assert config.for_agent(slug) == AgentConsensus(samples=1, block=1, report=1)
    for slug in ("tests", "consistency", "general"):
        assert config.for_agent(slug) == AgentConsensus(samples=1, block=None, report=1)


def test_samples_override_out_of_range_is_an_error() -> None:
    with pytest.raises(ConfigError, match="between 1 and 5"):
        load_config(None, samples_override=9)


def test_budget_caps_a_review_at_forty_calls() -> None:
    config = ConsensusConfig(samples=5, block="all", report="majority", agents={})
    slugs = [f"agent{i}" for i in range(MAX_TOTAL_CALLS // 5 + 1)]
    with pytest.raises(ConfigError, match="over the limit"):
        config.check_budget(slugs)


def test_max_samples_for_every_agent_stays_inside_the_budget() -> None:
    config = load_config(None, samples_override=5)
    slugs = [
        "bugs",
        "security",
        "performance",
        "tests",
        "docs",
        "constitution",
        "consistency",
        "general",
    ]
    assert config.total_calls(slugs) == 40
    config.check_budget(slugs)


def test_env_samples_parsing() -> None:
    assert samples_from_env(None) is None
    assert samples_from_env("") is None
    assert samples_from_env("  ") is None
    assert samples_from_env("2") == 2


def test_env_samples_must_be_an_integer() -> None:
    with pytest.raises(ConfigError, match="GATEHOUSE_SAMPLES"):
        samples_from_env("lots")


def _packaged(text: str) -> ConsensusConfig:
    """Load packaged defaults from ``text`` instead of the shipped file."""
    resource = MagicMock()
    resource.read_text.return_value = text
    files = MagicMock()
    files.joinpath.return_value = resource
    with patch("gatehouse.config.resources.files", return_value=files):
        return packaged_defaults()


def test_packaged_defaults_require_every_key() -> None:
    """defaults.toml is the only statement of what ships, so a gap is an error."""
    with pytest.raises(ConfigError, match=r"missing consensus\.block, consensus\.report"):
        _packaged("[consensus]\nsamples = 3\n")


def test_packaged_defaults_parse_per_agent_counts() -> None:
    config = _packaged(
        '[consensus]\nsamples = 2\nblock = "all"\nreport = "majority"\n'
        "[consensus.agents]\ntests = 1\n"
    )
    assert config.samples == 2
    assert config.samples_for("tests") == 1
    assert config.samples_for("bugs") == 2


def test_packaged_defaults_reject_an_unknown_agent() -> None:
    with pytest.raises(ConfigError, match="unknown agent"):
        _packaged(
            '[consensus]\nsamples = 3\nblock = "all"\nreport = "majority"\n'
            "[consensus.agents]\nbuggs = 1\n"
        )


def test_packaged_defaults_reject_an_out_of_range_agent_count() -> None:
    with pytest.raises(ConfigError, match="between 1 and 5"):
        _packaged(
            '[consensus]\nsamples = 3\nblock = "all"\nreport = "majority"\n'
            "[consensus.agents]\ntests = 9\n"
        )


def test_packaged_defaults_reject_a_non_table_consensus() -> None:
    with pytest.raises(ConfigError, match="must be a table"):
        _packaged("consensus = 3\n")
