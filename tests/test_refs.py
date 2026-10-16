"""The missing-ref oracle: GitHub, not the model's cutoff, says which refs exist."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from gatehouse.agents import get_agents
from gatehouse.github import format_review_body
from gatehouse.refs import cited_refs, ref_exists
from gatehouse.review import _drop_disproved, _filter_findings

AGENT = next(a for a in get_agents() if a.name == "Bug Hunter")

# The finding that recurred on every Dependabot major bump past the cutoff.
CHECKOUT_V7 = {
    "file": ".github/workflows/ci.yml",
    "lineStart": 12,
    "severity": "high",
    "confidence": 90,
    "description": "actions/checkout@v7 does not exist; the latest release is v4, "
    "so every job fails at checkout.",
    "suggestion": "Pin actions/checkout@v4.",
    "evidence": "- uses: actions/checkout@v7",
}


def _with(**changes: Any) -> dict[str, Any]:
    return {**CHECKOUT_V7, **changes}


def _resolver(known: dict[tuple[str, str, str], bool | None]):
    calls: list[tuple[str, str, str]] = []

    def resolve(owner: str, repo: str, ref: str) -> bool | None:
        calls.append((owner, repo, ref))
        return known.get((owner, repo, ref), False)

    return resolve, calls


def test_the_recurring_finding_cites_its_ref() -> None:
    # The suggested replacement is not what the claim is about.
    assert cited_refs(CHECKOUT_V7) == {("actions", "checkout", "v7")}


@pytest.mark.parametrize(
    "description",
    [
        "Tag v7 is not a valid release of actions/checkout@v7.",
        "actions/checkout@v7 is a nonexistent version.",
        "actions/checkout@v7 has not been released yet.",
        "No such tag: actions/checkout@v7.",
        "actions/checkout@v7 doesn't exist.",
    ],
)
def test_claim_wordings(description: str) -> None:
    assert ("actions", "checkout", "v7") in cited_refs(_with(description=description))


def test_reusable_workflow_path_is_parsed() -> None:
    finding = _with(
        description="Version v0 does not exist.",
        evidence="uses: crunchtools/gatehouse/.github/workflows/review.yml@v0",
    )
    assert cited_refs(finding) == {("crunchtools", "gatehouse", "v0")}


def test_a_finding_that_claims_nothing_missing_cites_nothing() -> None:
    finding = _with(description="actions/checkout@v7 runs with write permissions it does not need.")
    assert cited_refs(finding) == set()


@pytest.mark.parametrize(
    "description",
    [
        "The input `fetch-tags` does not exist on actions/checkout@v7.",
        "actions/checkout@v7 is pinned by tag. The cache key does not exist yet.",
        "The file ./build.sh referenced by actions/checkout@v7 does not exist.",
    ],
)
def test_a_claim_about_something_else_cites_nothing(description: str) -> None:
    assert cited_refs(_with(description=description, suggestion="")) == set()


def test_resolving_ref_drops_the_finding() -> None:
    resolve, _ = _resolver(
        {("actions", "checkout", "v7"): True, ("actions", "checkout", "v4"): True}
    )
    kept, dropped = _drop_disproved([(AGENT, [CHECKOUT_V7])], resolve)
    assert (kept, dropped) == ([(AGENT, [])], 1)


def test_a_ref_that_really_is_missing_keeps_the_finding() -> None:
    resolve, _ = _resolver({("actions", "checkout", "v4"): True})
    kept, dropped = _drop_disproved([(AGENT, [CHECKOUT_V7])], resolve)
    assert (kept, dropped) == ([(AGENT, [CHECKOUT_V7])], 0)


def test_an_unanswered_lookup_keeps_the_finding() -> None:
    resolve, _ = _resolver(
        {("actions", "checkout", "v7"): None, ("actions", "checkout", "v4"): True}
    )
    _, dropped = _drop_disproved([(AGENT, [CHECKOUT_V7])], resolve)
    assert dropped == 0


def test_each_ref_is_looked_up_once() -> None:
    resolve, calls = _resolver(
        {("actions", "checkout", "v7"): True, ("actions", "checkout", "v4"): True}
    )
    _drop_disproved([(AGENT, [CHECKOUT_V7, dict(CHECKOUT_V7, lineStart=20)])], resolve)
    assert calls == [("actions", "checkout", "v7")]


def test_other_findings_are_never_looked_up() -> None:
    resolve, calls = _resolver({})
    finding = _with(description="The token is logged.", evidence="echo $TOKEN")
    kept, dropped = _drop_disproved([(AGENT, [finding])], resolve)
    assert (kept, dropped, calls) == ([(AGENT, [finding])], 0, [])


def test_filter_findings_reports_the_count() -> None:
    resolve, _ = _resolver(
        {("actions", "checkout", "v7"): True, ("actions", "checkout", "v4"): True}
    )
    haystack = "-uses:actions/checkout@v7"
    _, counts = _filter_findings([(AGENT, [CHECKOUT_V7])], haystack, [], resolve=resolve)
    assert counts["disproved"] == 1


def test_review_body_says_why() -> None:
    body = format_review_body([(AGENT, [])], disproved=2, agent_count=1)
    assert "2 finding(s) dropped: the ref they call missing exists on GitHub." in body


@pytest.mark.parametrize(
    ("status", "expected"), [(200, True), (404, False), (422, False), (403, None)]
)
def test_ref_exists_maps_status(status: int, expected: bool | None) -> None:
    response = httpx.Response(status, request=httpx.Request("GET", "https://api.github.com/x"))
    with patch("gatehouse.refs.httpx.get", return_value=response) as get:
        assert ref_exists("actions", "checkout", "v7") is expected
    assert get.call_args.args[0] == "https://api.github.com/repos/actions/checkout/commits/v7"


def test_ref_exists_survives_network_errors() -> None:
    with patch("gatehouse.refs.httpx.get", side_effect=httpx.ConnectError("down")):
        assert ref_exists("actions", "checkout", "v7") is None
