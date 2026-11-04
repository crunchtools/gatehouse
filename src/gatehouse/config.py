"""Consensus configuration: packaged defaults overlaid by the repo's gatehouse.toml.

The reviewed repository's copy is loaded by the caller, which under the
fork-safe workflow reads it from the trusted base — a pull request must not be
able to weaken the gate that reviews it. This module only parses and validates.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from importlib import resources
from typing import Any

from gatehouse.agents import AGENT_BY_SLUG

CONFIG_FILE = "gatehouse.toml"
DEFAULTS_RESOURCE = "defaults.toml"

MIN_SAMPLES = 1
MAX_SAMPLES = 5
MAX_TOTAL_CALLS = 40

ALL = "all"
MAJORITY = "majority"

Threshold = str | int


class ConfigError(ValueError):
    """A config the run cannot proceed under. The CLI turns this into exit 2."""


@dataclass(frozen=True)
class AgentConsensus:
    """One agent's resolved sample count and vote thresholds.

    ``block`` is None for an advisory agent: it cannot block whatever it
    finds, so a blocking threshold has no meaning for it and an unreachable
    one is not a conflict worth failing the run over.
    """

    samples: int
    block: int | None
    report: int


@dataclass(frozen=True)
class ConsensusConfig:
    """Sample counts and thresholds for a review.

    ``samples`` is the count every agent uses unless ``agents`` overrides it by
    slug. ``block`` and ``report`` are unresolved thresholds ("all",
    "majority" or an integer); each agent resolves them against its own count,
    so one setting means the same thing at every sample count.
    """

    samples: int
    block: Threshold
    report: Threshold
    agents: dict[str, int]

    def samples_for(self, slug: str) -> int:
        """The sample count for one agent slug."""
        return self.agents.get(slug, self.samples)

    def for_agent(self, slug: str, finished: int | None = None) -> AgentConsensus:
        """Resolve this agent's thresholds against its sample count.

        ``finished`` is how many of its samples came back, and thresholds
        resolve against that: when two of three samples return and both see a
        critical finding, "all" means two, because two is every sample there
        was. A lost sample is not a vote against a finding.

        An agent not in AGENT_BY_SLUG is treated as blocking, so a caller that
        invents a slug still gets its threshold validated.
        """
        n = self.samples_for(slug) if finished is None else max(finished, 1)
        agent = AGENT_BY_SLUG.get(slug)
        blocking = agent is None or agent.blocking
        return AgentConsensus(
            samples=n,
            block=resolve_threshold(self.block, n, "block", slug) if blocking else None,
            report=resolve_threshold(self.report, n, "report", slug),
        )

    def total_calls(self, slugs: list[str]) -> int:
        """How many model calls a review of these agents will make."""
        return sum(self.samples_for(slug) for slug in slugs)

    def check_budget(self, slugs: list[str]) -> None:
        """Raise ConfigError when these agents would exceed MAX_TOTAL_CALLS."""
        total = self.total_calls(slugs)
        if total > MAX_TOTAL_CALLS:
            msg = (
                f"consensus would make {total} model calls, over the limit of "
                f"{MAX_TOTAL_CALLS}; lower samples or review fewer agents"
            )
            raise ConfigError(msg)


def resolve_threshold(spec: Threshold, samples: int, field: str, slug: str) -> int:
    """Turn a threshold spec into a vote count for an agent with ``samples`` samples.

    "all" is unanimity, "majority" is strictly more than half (2 of 3, 3 of 4),
    and an integer is itself. An integer above the agent's sample count can
    never be reached, which would silence that agent: that is a config error,
    not a clamp.
    """
    if isinstance(spec, bool):  # bools are ints in Python; never a vote count
        msg = f"consensus.{field} must be {ALL!r}, {MAJORITY!r} or an integer, not a boolean"
        raise ConfigError(msg)
    if isinstance(spec, int):
        if spec < 1:
            msg = f"consensus.{field} must be at least 1, got {spec}"
            raise ConfigError(msg)
        if spec > samples:
            msg = (
                f"consensus.{field} is {spec} but agent {slug!r} runs {samples} "
                f"sample(s), so the threshold can never be reached"
            )
            raise ConfigError(msg)
        return spec
    if spec == ALL:
        return samples
    if spec == MAJORITY:
        return samples // 2 + 1
    msg = f"consensus.{field} must be {ALL!r}, {MAJORITY!r} or an integer, got {spec!r}"
    raise ConfigError(msg)


def _table(document: dict[str, Any], key: str) -> dict[str, Any]:
    """One table from a parsed TOML document, or an empty one."""
    value = document.get(key, {})
    if not isinstance(value, dict):
        msg = f"[{key}] must be a table, got {type(value).__name__}"
        raise ConfigError(msg)
    return value


def _samples(value: Any, where: str) -> int:
    """Validate one sample count."""
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{where} must be an integer, got {value!r}"
        raise ConfigError(msg)
    if not MIN_SAMPLES <= value <= MAX_SAMPLES:
        msg = f"{where} must be between {MIN_SAMPLES} and {MAX_SAMPLES}, got {value}"
        raise ConfigError(msg)
    return value


def _parse(text: str, source: str) -> dict[str, Any]:
    """Parse TOML, naming the source in the error."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = f"{source} is not valid TOML: {exc}"
        raise ConfigError(msg) from exc


def packaged_defaults() -> ConsensusConfig:
    """The shipped configuration, from defaults.toml next to this module."""
    text = resources.files("gatehouse").joinpath(DEFAULTS_RESOURCE).read_text()
    return _build(_parse(text, DEFAULTS_RESOURCE), ConsensusConfig(3, ALL, MAJORITY, {}))


def _build(document: dict[str, Any], base: ConsensusConfig) -> ConsensusConfig:
    """Overlay a parsed document on ``base``, key by key."""
    consensus = _table(document, "consensus")
    agents = dict(base.agents)
    for slug, value in _table(consensus, "agents").items():
        if slug not in AGENT_BY_SLUG:
            valid = ", ".join(AGENT_BY_SLUG)
            msg = f"unknown agent {slug!r} in [consensus.agents]; valid slugs: {valid}"
            raise ConfigError(msg)
        agents[slug] = _samples(value, f"consensus.agents.{slug}")

    overlay: dict[str, Any] = {"agents": agents}
    if "samples" in consensus:
        overlay["samples"] = _samples(consensus["samples"], "consensus.samples")
    for field in ("block", "report"):
        if field in consensus:
            overlay[field] = consensus[field]
    return replace(base, **overlay)


def load_config(
    repo_toml: str | None = None, samples_override: int | None = None
) -> ConsensusConfig:
    """The configuration for a review: packaged defaults, the repo's file, then the override.

    ``repo_toml`` is the text of the reviewed repository's gatehouse.toml, or
    None when it has none. ``samples_override`` is the global count from
    --samples or GATEHOUSE_SAMPLES; it replaces every per-agent override too,
    so `--samples 1` reproduces single-sample behaviour for all agents.

    Thresholds are validated here for every agent, so an unreachable one fails
    the run before any model is called.
    """
    config = packaged_defaults()
    if repo_toml is not None:
        config = _build(_parse(repo_toml, CONFIG_FILE), config)
    if samples_override is not None:
        config = replace(config, samples=_samples(samples_override, "--samples"), agents={})
    for slug in AGENT_BY_SLUG:
        config.for_agent(slug)
    return config


def samples_from_env(value: str | None) -> int | None:
    """Parse GATEHOUSE_SAMPLES, or None when it is unset or empty."""
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        msg = f"GATEHOUSE_SAMPLES must be an integer, got {value!r}"
        raise ConfigError(msg) from exc
