"""Clustering and vote tallying across an agent's samples.

Each agent reviews the same diff several times. The samples are unioned for
recall, grouped into clusters that stand for one finding, and tallied
additively: a cluster's votes are how many samples saw it. Nothing penalizes
anything else — a tally that lets one signal cancel another is how ROTV's
date consensus once dated a January article to April.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from gatehouse.agents import Agent
    from gatehouse.config import AgentConsensus

SEVERITY_ORDER: tuple[str, ...] = ("low", "medium", "high", "critical")
BLOCKING_SEVERITIES = frozenset({"critical", "high"})

VOTES_KEY = "votes"
SAMPLES_KEY = "samples"
BLOCKING_VOTES_KEY = "blockingVotes"
BLOCKS_KEY = "blocksTheRun"
MEMBER_LINES_KEY = "memberLines"


def severity_rank(severity: str) -> int:
    """Where a severity sits in the order; an unknown one ranks lowest."""
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return 0


def _confidence(finding: dict[str, Any]) -> int:
    value = finding.get("confidence", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _severity(finding: dict[str, Any]) -> str:
    value = finding.get("severity", "low")
    return value if isinstance(value, str) else "low"


class _Cluster:
    """One finding as seen by one or more samples of the same agent."""

    def __init__(self, finding: dict[str, Any], evidence: str, sample: int) -> None:
        self.members: list[dict[str, Any]] = [finding]
        self.evidence = evidence
        self.voters: set[int] = {sample}
        self.blocking_voters: set[int] = (
            {sample} if _severity(finding) in BLOCKING_SEVERITIES else set()
        )

    def matches(self, finding: dict[str, Any], evidence: str) -> bool:
        """True when this finding is the same one, by file, category and quoted code.

        Containment, not equality: two samples quoting different spans of one
        bug — a line and the three lines around it — are the same finding. An
        equality or hash test would split them, and a unanimity threshold would
        then never be reached.
        """
        first = self.members[0]
        if finding.get("file", "") != first.get("file", ""):
            return False
        if finding.get("category", "") != first.get("category", ""):
            return False
        return evidence in self.evidence or self.evidence in evidence

    def add(self, finding: dict[str, Any], evidence: str, sample: int) -> None:
        """Count this finding into the cluster; one sample votes at most once."""
        self.members.append(finding)
        self.voters.add(sample)
        if _severity(finding) in BLOCKING_SEVERITIES:
            self.blocking_voters.add(sample)
        if len(evidence) > len(self.evidence):
            self.evidence = evidence

    def representative(self, samples: int) -> dict[str, Any]:
        """The cluster as one finding: its clearest member, with the vote counts.

        The text, line numbers and suggestion come from the most confident
        member, because that is the one that explains the problem best; every
        member's line range is kept too, so a filter that needs a line inside
        the diff can use one of the others instead of dropping the finding. The
        severity is the most severe any member gave it, so what is displayed
        can never be milder than what the blocking decision was made on.
        """
        best = max(self.members, key=_confidence)
        finding = dict(best)
        finding["severity"] = max((_severity(member) for member in self.members), key=severity_rank)
        finding["confidence"] = _confidence(best)
        finding[VOTES_KEY] = len(self.voters)
        finding[BLOCKING_VOTES_KEY] = len(self.blocking_voters)
        finding[SAMPLES_KEY] = samples
        finding[MEMBER_LINES_KEY] = sorted(
            {
                (member.get("lineStart", 0), member.get("lineEnd", 0))
                for member in self.members
                if isinstance(member.get("lineStart"), int)
            }
        )
        return finding


def cluster_samples(
    samples: list[list[dict[str, Any]]],
    *,
    squash: Callable[[str], str],
    min_evidence: int,
) -> list[dict[str, Any]]:
    """Cluster one agent's samples into findings carrying vote counts.

    ``squash`` strips whitespace from a quote and ``min_evidence`` is the
    shortest quote worth matching on. A finding whose evidence is too short to
    match stands alone with a single vote and no blocking vote: three unrelated
    findings in one file with nothing quotable must not add up to a unanimous
    one, and a claim with nothing quotable must not fail a run even where one
    vote is configured to block.
    """
    clusters: list[_Cluster] = []
    singletons: list[_Cluster] = []
    for index, findings in enumerate(samples):
        for finding in findings:
            evidence = squash(str(finding.get("evidence", "")))
            if len(evidence) < min_evidence:
                singletons.append(_Cluster(finding, evidence, index))
                continue
            for cluster in clusters:
                # A sample that reports two findings found two things, however
                # much their quotes overlap. Only a later sample's finding
                # joins an existing cluster.
                if index not in cluster.voters and cluster.matches(finding, evidence):
                    cluster.add(finding, evidence, index)
                    break
            else:
                clusters.append(_Cluster(finding, evidence, index))

    total = len(samples)
    reported = [cluster.representative(total) for cluster in clusters]
    for cluster in singletons:
        finding = cluster.representative(total)
        finding[VOTES_KEY] = 1
        finding[BLOCKING_VOTES_KEY] = 0
        reported.append(finding)
    return reported


def keep_reported(
    findings: list[dict[str, Any]], consensus: AgentConsensus
) -> list[dict[str, Any]]:
    """The findings with enough votes to be shown at all."""
    return [f for f in findings if int(f.get(VOTES_KEY, 0)) >= consensus.report]


def blocks_the_run(finding: dict[str, Any]) -> bool:
    """The verdict recorded during the tally; absent means it was not reached.

    The verdict is made once, by blocks(), where the agent's thresholds and its
    finished sample count are both in hand. Reading it here rather than judging
    severity again is what stops a finding blocking a run it was never shown in.
    """
    return bool(finding.get(BLOCKS_KEY, False))


def blocks(agent: Agent, finding: dict[str, Any], consensus: AgentConsensus) -> bool:
    """True when this cluster fails the run.

    It takes a blocking agent, a critical or high severity, and enough samples
    that called it that: votes for a milder severity are not votes to block.
    """
    if not agent.blocking or consensus.block is None:
        return False
    if _severity(finding) not in BLOCKING_SEVERITIES:
        return False
    votes = finding.get(BLOCKING_VOTES_KEY, 0)
    return isinstance(votes, int) and votes >= consensus.block
