"""Tests for clustering across samples and the vote tally."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from gatehouse.agents import BUG_HUNTER, GENERAL
from gatehouse.config import CONFIG_FILE, AgentConsensus, load_config
from gatehouse.consensus import (
    BLOCKING_VOTES_KEY,
    BLOCKS_KEY,
    MEMBER_LINES_KEY,
    SAMPLES_KEY,
    VOTES_KEY,
    blocks,
    cluster_samples,
    keep_reported,
    severity_rank,
)
from gatehouse.llm import Completion
from gatehouse.review import AgentOutcome, _squash, _tally, run_review

DIFF = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,4 @@
-my_var = 1
+myVar = 1
 x = get_data()
+user.name.lower()
+total = total / count
"""


def finding(
    evidence: str,
    *,
    severity: str = "high",
    category: str = "bug",
    file: str = "src/app.py",
    confidence: int = 90,
    description: str = "a problem",
    line: int = 3,
) -> dict[str, Any]:
    return {
        "file": file,
        "lineStart": line,
        "lineEnd": line,
        "severity": severity,
        "category": category,
        "description": description,
        "suggestion": "fix it",
        "evidence": evidence,
        "confidence": confidence,
    }


def cluster(samples: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return cluster_samples(samples, squash=_squash, min_evidence=4)


def test_longer_and_shorter_quotes_of_one_bug_cluster() -> None:
    """A sample quoting one line and a sample quoting three are one finding."""
    short = [finding("total = total / count")]
    long = [finding("x = get_data()\ntotal = total / count\nuser.name.lower()")]
    clusters = cluster([short, long])
    assert len(clusters) == 1
    assert clusters[0][VOTES_KEY] == 2
    assert clusters[0][SAMPLES_KEY] == 2


def test_reformatted_quote_still_clusters() -> None:
    """Whitespace is not part of the match, so a reflowed quote is the same quote."""
    clusters = cluster([[finding("total = total / count")], [finding("total=total/count")]])
    assert len(clusters) == 1
    assert clusters[0][VOTES_KEY] == 2


def test_unrelated_findings_in_one_file_do_not_cluster() -> None:
    clusters = cluster([[finding("total = total / count")], [finding("user.name.lower()")]])
    assert len(clusters) == 2
    assert [c[VOTES_KEY] for c in clusters] == [1, 1]


def test_two_findings_in_the_same_sample_never_merge() -> None:
    """One sample reporting two findings found two things, however much they overlap."""
    both = [
        finding("total = total / count"),
        finding("x = get_data()\ntotal = total / count", description="another problem"),
    ]
    clusters = cluster([both])
    assert len(clusters) == 2
    assert [c[VOTES_KEY] for c in clusters] == [1, 1]


def test_a_later_sample_joins_only_one_cluster() -> None:
    first = [
        finding("total = total / count"),
        finding("user.name.lower()", description="another problem"),
    ]
    second = [finding("total = total / count")]
    clusters = cluster([first, second])
    assert sorted(c[VOTES_KEY] for c in clusters) == [1, 2]


def test_same_quote_in_another_category_is_another_finding() -> None:
    clusters = cluster(
        [[finding("total = total / count")], [finding("total = total / count", category="quality")]]
    )
    assert len(clusters) == 2


def test_same_quote_in_another_file_is_another_finding() -> None:
    clusters = cluster(
        [[finding("total = total / count")], [finding("total = total / count", file="src/b.py")]]
    )
    assert len(clusters) == 2


def test_findings_without_usable_evidence_never_add_up() -> None:
    """Three unevidenced findings in one file must not tally into a unanimous one."""
    samples = [[finding("")], [finding("")], [finding("...")]]
    clusters = cluster(samples)
    assert len(clusters) == 3
    assert all(c[VOTES_KEY] == 1 for c in clusters)
    assert all(c[BLOCKING_VOTES_KEY] == 0 for c in clusters)


def test_cluster_severity_is_the_most_severe_member() -> None:
    """What is displayed is never milder than what the blocking decision saw."""
    samples = [
        [finding("total = total / count", severity="medium")],
        [finding("total = total / count", severity="high")],
    ]
    assert cluster(samples)[0]["severity"] == "high"


def test_blocking_votes_count_only_the_samples_that_called_it_blocking() -> None:
    samples = [
        [finding("total = total / count", severity="high")],
        [finding("total = total / count", severity="medium")],
        [finding("total = total / count", severity="medium")],
    ]
    one = cluster(samples)[0]
    assert one[VOTES_KEY] == 3
    assert one[BLOCKING_VOTES_KEY] == 1


def test_representative_text_comes_from_the_most_confident_member() -> None:
    samples = [
        [finding("total = total / count", confidence=81, description="vague", line=9)],
        [finding("total = total / count", confidence=97, description="precise", line=4)],
    ]
    one = cluster(samples)[0]
    assert one["description"] == "precise"
    assert one["lineStart"] == 4
    assert one["confidence"] == 97


def test_severity_rank_puts_an_unknown_severity_lowest() -> None:
    assert severity_rank("critical") > severity_rank("high") > severity_rank("low")
    assert severity_rank("spicy") == severity_rank("low")


def test_keep_reported_drops_a_single_voter_at_majority() -> None:
    consensus = AgentConsensus(samples=3, block=3, report=2)
    findings = [
        {VOTES_KEY: 1, "description": "one sample saw it"},
        {VOTES_KEY: 2, "description": "two samples saw it"},
    ]
    kept = keep_reported(findings, consensus)
    assert [f["description"] for f in kept] == ["two samples saw it"]


def test_blocks_needs_unanimity_a_blocking_agent_and_a_blocking_severity() -> None:
    consensus = AgentConsensus(samples=3, block=3, report=2)
    unanimous = {"severity": "high", BLOCKING_VOTES_KEY: 3}
    split = {"severity": "high", BLOCKING_VOTES_KEY: 2}
    mild = {"severity": "medium", BLOCKING_VOTES_KEY: 3}
    assert blocks(BUG_HUNTER, unanimous, consensus) is True
    assert blocks(BUG_HUNTER, split, consensus) is False
    assert blocks(BUG_HUNTER, mild, consensus) is False


def test_an_advisory_agent_never_blocks() -> None:
    consensus = AgentConsensus(samples=1, block=None, report=1)
    assert blocks(GENERAL, {"severity": "critical", BLOCKING_VOTES_KEY: 1}, consensus) is False


def _reply(findings: list[dict[str, Any]]) -> Completion:
    return Completion(json.dumps(findings))


@pytest.mark.asyncio
async def test_a_high_only_one_sample_saw_does_not_block_or_report() -> None:
    """The three-runs-three-different-HIGHs case: one voter is not a finding."""
    replies = [
        _reply([finding("total = total / count")]),
        _reply([]),
        _reply([]),
    ]

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, side_effect=replies),
        patch("gatehouse.review.format_results") as formatted,
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 0
    assert formatted.call_args.args[0] == [(BUG_HUNTER, [])]


@pytest.mark.asyncio
async def test_a_high_every_sample_saw_blocks() -> None:
    replies = [_reply([finding("total = total / count")]) for _ in range(3)]

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, side_effect=replies),
        patch("gatehouse.review.format_results") as formatted,
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 1
    reported = formatted.call_args.args[0][0][1]
    assert len(reported) == 1
    assert reported[0][VOTES_KEY] == 3


@pytest.mark.asyncio
async def test_a_two_of_three_high_is_reported_but_does_not_block() -> None:
    """Majority reports, unanimity blocks: a split HIGH reaches a human without stopping work."""
    replies = [
        _reply([finding("total = total / count")]),
        _reply([finding("total = total / count")]),
        _reply([]),
    ]

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, side_effect=replies),
        patch("gatehouse.review.format_results") as formatted,
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 0
    reported = formatted.call_args.args[0][0][1]
    assert [f[VOTES_KEY] for f in reported] == [2]


@pytest.mark.asyncio
async def test_samples_one_blocks_on_a_single_finding_as_before() -> None:
    """--samples 1 is the pre-consensus gate, unanimity of one."""
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch(
            "gatehouse.review.call_model",
            new_callable=AsyncMock,
            return_value=_reply([finding("total = total / count")]),
        ),
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k", samples=1)
    assert exit_code == 1


def test_config_and_tally_agree_on_what_blocks() -> None:
    """The shipped config means 3/3 to block and 2/3 to report, for every blocking agent."""
    config = load_config(None)
    for slug in ("bugs", "security", "performance", "docs", "constitution"):
        consensus = config.for_agent(slug)
        assert (consensus.samples, consensus.block, consensus.report) == (3, 3, 2)


def test_an_unevidenced_finding_cannot_block_even_where_one_vote_blocks() -> None:
    """A claim with nothing quotable must not fail a run, however low the threshold."""
    consensus = AgentConsensus(samples=1, block=1, report=1)
    unevidenced = cluster([[finding("", severity="critical")]])[0]
    assert unevidenced[VOTES_KEY] == 1
    assert blocks(BUG_HUNTER, unevidenced, consensus) is False


def test_thresholds_resolve_against_the_samples_that_finished() -> None:
    """Two of three samples returning means "all" is two, not three."""
    config = load_config(None)
    assert config.for_agent("bugs", finished=2) == AgentConsensus(samples=2, block=2, report=2)
    assert config.for_agent("bugs", finished=3) == AgentConsensus(samples=3, block=3, report=2)


@pytest.mark.asyncio
async def test_two_finished_samples_agreeing_on_a_high_still_block() -> None:
    """A lost sample does not raise the bar for the samples that came back."""
    calls = {"n": 0}

    async def fake(*_a: Any, **_k: Any) -> Completion:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("too slow")
        return _reply([finding("total = total / count")])

    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.call_model", side_effect=fake),
        patch("gatehouse.review.format_results") as formatted,
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 1
    reported = formatted.call_args.args[0][0][1]
    assert reported[0][VOTES_KEY] == 2
    assert reported[0][SAMPLES_KEY] == 2


@pytest.mark.asyncio
async def test_a_blocking_cluster_short_of_the_report_threshold_is_still_shown() -> None:
    """A run must never fail on a finding it did not show."""

    def context(relpath: str) -> str | None:
        if relpath != CONFIG_FILE:
            return None
        return (
            "[consensus]\nsamples = 3\nblock = 1\nreport = 3\n"
            "[consensus.agents]\ntests = 3\nconsistency = 3\ngeneral = 3\n"
        )

    replies = [_reply([finding("total = total / count")]), _reply([]), _reply([])]
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review._load_context_file", side_effect=context),
        patch("gatehouse.review.call_model", new_callable=AsyncMock, side_effect=replies),
        patch("gatehouse.review.format_results") as formatted,
    ):
        exit_code = await run_review(stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k")
    assert exit_code == 1
    reported = formatted.call_args.args[0][0][1]
    assert [f[VOTES_KEY] for f in reported] == [1]


def test_a_cluster_keeps_every_members_line_range() -> None:
    samples = [
        [finding("total = total / count", line=4, confidence=99)],
        [finding("total = total / count", line=5, confidence=80)],
    ]
    assert cluster(samples)[0][MEMBER_LINES_KEY] == [(4, 4), (5, 5)]


def test_every_tallied_finding_carries_the_blocking_verdict() -> None:
    """_has_blocking_findings has no fallback, so the tally must always decide."""
    outcome = AgentOutcome(
        agent=BUG_HUNTER,
        samples=[[finding("total = total / count")] for _ in range(3)],
        replies=[],
        reason=None,
    )
    results = _tally([outcome], load_config(None))
    assert results[0][1]
    assert all(BLOCKS_KEY in f for f in results[0][1])


@pytest.mark.asyncio
async def test_a_cluster_is_reanchored_to_a_member_line_inside_the_diff() -> None:
    """The most confident sample may quote a line the PR diff does not show."""
    replies = [
        _reply([finding("total = total / count", line=99, confidence=99)]),
        _reply([finding("total = total / count", line=5, confidence=90)]),
        _reply([finding("total = total / count", line=5, confidence=85)]),
    ]
    with (
        patch("gatehouse.review.get_file_listing", return_value=None),
        patch("gatehouse.review.load_styleguide", return_value=None),
        patch("gatehouse.review.right_side_lines", return_value={"src/app.py": {5}}),
        patch("gatehouse.review.fetch_answered_threads", return_value=[]),
        patch("gatehouse.review.post_pr_review", new_callable=AsyncMock) as post,
        patch("gatehouse.review.call_model", new_callable=AsyncMock, side_effect=replies),
    ):
        await run_review(
            stdin_diff=DIFF, agent_slugs=["bugs"], api_key="k", comment=True, advisory=True
        )
    posted = post.call_args.args[0][0][1]
    assert len(posted) == 1
    assert posted[0]["lineStart"] == 5
    assert post.call_args.kwargs["offdiff"] == 0
