"""The triage filter in triage.yml, run against fixture comment lists.

The filter is the whole of the merge condition, so it is tested by executing
it — extracted from the workflow between its marker comments, so the test
cannot drift from what CI actually runs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TRIAGE = Path(__file__).parent.parent / ".github" / "workflows" / "triage.yml"
EXAMPLE = Path(__file__).parent.parent / "examples" / "gatehouse.yml"
BOT = "github-actions[bot]"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _comment(
    cid: int, user: str, reply_to: int | None = None, header: str = "**HIGH** (Bug Hunter)"
) -> dict:
    return {
        "id": cid,
        "user": {"login": user},
        "in_reply_to_id": reply_to,
        "html_url": f"https://example.test/c/{cid}",
        "path": "src/app.py",
        "line": 10,
        "original_line": 10,
        "body": f"{header}: finding {cid}\nmore",
    }


def _unanswered(*pages: list[dict], required_low: str = "Bug Hunter,Security Scan") -> list[dict]:
    block = re.search(
        r"# --- triage filter.*?\n(.*?)# --- end triage filter ---",
        TRIAGE.read_text(),
        re.S,
    )
    assert block, "triage filter markers missing from triage.yml"
    program = re.search(r"jq -s --arg bot .*? '(.*?)'", block.group(1), re.S)
    assert program, "jq program not found between the markers"
    # gh api --paginate emits one JSON array per page; -s slurps them.
    stdin = "".join(json.dumps(page) for page in pages)
    out = subprocess.run(
        ["jq", "-s", "--arg", "bot", BOT, "--arg", "required_low", required_low, program.group(1)],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_no_comments_passes() -> None:
    assert _unanswered([]) == []


def test_unanswered_finding_is_reported() -> None:
    result = _unanswered([_comment(1, BOT)])
    assert [r["url"] for r in result] == ["https://example.test/c/1"]
    assert result[0]["finding"] == "**HIGH** (Bug Hunter): finding 1"


def test_human_reply_answers_the_finding() -> None:
    assert _unanswered([_comment(1, BOT), _comment(2, "maintainer", reply_to=1)]) == []


def test_reviewer_replying_to_itself_does_not_count() -> None:
    assert len(_unanswered([_comment(1, BOT), _comment(2, BOT, reply_to=1)])) == 1


def test_reply_on_another_page_counts() -> None:
    assert _unanswered([_comment(1, BOT)], [_comment(2, "maintainer", reply_to=1)]) == []


def test_only_the_unanswered_one_is_reported() -> None:
    result = _unanswered(
        [
            _comment(1, BOT),
            _comment(2, BOT),
            _comment(3, "maintainer", reply_to=2),
            _comment(4, "maintainer"),  # a human's own review comment is not a finding
        ]
    )
    assert [r["url"] for r in result] == ["https://example.test/c/1"]


def test_outdated_finding_uses_original_line() -> None:
    finding = _comment(1, BOT)
    finding["line"] = None
    assert _unanswered([finding])[0]["line"] == 10


@pytest.mark.parametrize("agent", ["Bug Hunter", "Security Scan"])
def test_low_from_required_agent_needs_an_answer(agent: str) -> None:
    assert len(_unanswered([_comment(1, BOT, header=f"**LOW** ({agent})")])) == 1


@pytest.mark.parametrize("agent", ["Documentation", "Consistency Check", "Test Coverage"])
def test_low_from_other_agents_is_advisory(agent: str) -> None:
    assert _unanswered([_comment(1, BOT, header=f"**LOW** ({agent})")]) == []


def test_medium_from_advisory_agent_still_needs_an_answer() -> None:
    assert len(_unanswered([_comment(1, BOT, header="**MEDIUM** (Documentation)")])) == 1


def test_required_low_agents_is_configurable() -> None:
    comments = [
        _comment(1, BOT, header="**LOW** (Bug Hunter)"),
        _comment(2, BOT, header="**LOW** (Documentation)"),
    ]
    result = _unanswered(comments, required_low=" Documentation ,Test Coverage")
    assert [r["url"] for r in result] == ["https://example.test/c/2"]
    assert _unanswered(comments, required_low="") == []


def test_low_in_a_later_line_does_not_make_a_finding_advisory() -> None:
    finding = _comment(1, BOT)
    finding["body"] += "\n**LOW** (Documentation): quoted"
    assert len(_unanswered([finding])) == 1


def test_triage_is_read_only() -> None:
    text = TRIAGE.read_text()
    assert "pull-requests: read" in text
    assert "write" not in re.sub(r"#.*", "", text)
    assert "actions/checkout" not in text
    assert "secrets" not in re.sub(r"#.*", "", text)


def test_example_runs_triage_after_review_and_on_replies() -> None:
    text = EXAMPLE.read_text()
    assert "pull_request_review_comment:" in text
    assert re.search(r"triage:.*?needs: review.*?if: always\(\)", text, re.S)
    # Only the review is gated to the PR event; the guard also runs on replies
    # (see test_fork_safety.test_guard_runs_on_reply_events).
    assert text.count("if: github.event_name == 'pull_request_target'") == 1


def test_example_guard_gates_on_head_repo_not_author_role() -> None:
    """A fork is what pull_request_target guards against; author role is not.

    author_association trusted a collaborator pushing from their own fork and
    distrusted Dependabot, whose branches live in the repo.
    """
    guard = EXAMPLE.read_text().split("  review:")[0]
    assert '[ "$HEAD_REPO" = "$GITHUB_REPOSITORY" ]' in guard
    assert "head.repo.full_name" in guard
    assert "author_association" not in guard


RETRIAGE = TRIAGE.parent / "retriage.yml"
RETRIAGE_EXAMPLE = EXAMPLE.parent / "gatehouse-retriage.yml"


def test_retriage_reruns_only_the_pull_request_target_suite() -> None:
    """Reply-event checks do not count toward branch rules (#47, #50)."""
    text = RETRIAGE.read_text()
    assert "event=pull_request_target" in text
    assert "workflow_run.workflow_id" in text
    assert "/rerun" in text
    assert "rerun-failed-jobs" not in text  # only triage, never a paid review


def test_retriage_is_least_privilege() -> None:
    code = re.sub(r"#.*", "", RETRIAGE.read_text())
    assert re.findall(r"^\s+(\w[\w-]*): (?:read|write)$", code, re.M) == ["actions"]
    assert "actions/checkout" not in code
    assert "secrets" not in code


def test_retriage_example_listens_for_reply_runs_only() -> None:
    text = RETRIAGE_EXAMPLE.read_text()
    assert "workflow_run:" in text
    assert "workflows: [Gatehouse]" in text
    assert "github.event.workflow_run.event == 'pull_request_review_comment'" in text
    assert "retriage.yml@v" in text


def test_required_low_agents_input_reaches_the_filter() -> None:
    text = TRIAGE.read_text()
    lines = text.splitlines()
    start = lines.index("      required_low_agents:")
    assert '        default: "Bug Hunter,Security Scan"' in lines[start : start + 8]
    assert "REQUIRED_LOW_AGENTS: ${{ inputs.required_low_agents }}" in text
    assert '--arg required_low "$REQUIRED_LOW_AGENTS"' in text


def test_review_takes_the_same_required_low_agents() -> None:
    """The review's advisory LOW cap must not hide a LOW triage requires."""
    text = (TRIAGE.parent / "review.yml").read_text()
    lines = text.splitlines()
    start = lines.index("      required_low_agents:")
    assert '        default: "Bug Hunter,Security Scan"' in lines[start : start + 8]
    assert "REQUIRED_LOW_AGENTS: ${{ inputs.required_low_agents }}" in text
    assert '--required-low-agents "$REQUIRED_LOW_AGENTS"' in text
