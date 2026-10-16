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
import tempfile
from pathlib import Path

import pytest

from gatehouse.github import REVIEW_FAILED_MARKER, REVIEW_MARKER

TRIAGE = Path(__file__).parent.parent / ".github" / "workflows" / "triage.yml"
EXAMPLE = Path(__file__).parent.parent / "examples" / "gatehouse.yml"
BOT = "github-actions[bot]"
SHA = "0123456789abcdef0123456789abcdef01234567"
ISSUE = "https://github.com/crunchtools/gatehouse/issues/90"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _comment(
    cid: int,
    user: str,
    reply_to: int | None = None,
    header: str = "**HIGH** (Bug Hunter)",
    body: str | None = None,
) -> dict:
    if body is None:
        body = f"{header}: finding {cid}\nmore" if reply_to is None else f"fixed in {SHA}"
    return {
        "id": cid,
        "user": {"login": user},
        "in_reply_to_id": reply_to,
        "html_url": f"https://example.test/c/{cid}",
        "path": "src/app.py",
        "line": 10,
        "original_line": 10,
        "body": body,
    }


def _unanswered(
    *pages: list[dict],
    required_low: str = "Bug Hunter,Security Scan",
    shas: tuple[str, ...] = (SHA,),
    open_issues: tuple[str, ...] = (ISSUE,),
) -> list[dict]:
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
    with tempfile.TemporaryDirectory() as tmp:
        files = {"shas": list(shas), "open_issues": list(open_issues)}
        args = []
        for name, value in files.items():
            path = Path(tmp) / f"{name}.json"
            path.write_text(json.dumps(value))
            args += ["--slurpfile", name, str(path)]
        out = subprocess.run(
            [
                "jq",
                "-s",
                "--arg",
                "bot",
                BOT,
                "--arg",
                "required_low",
                required_low,
                *args,
                program.group(1),
            ],
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
    return json.loads(out.stdout)


def _answered_by(body: str, **kwargs: tuple[str, ...]) -> bool:
    """Does a maintainer reply with this body answer the finding?"""
    return _unanswered([_comment(1, BOT), _comment(2, "maintainer", 1, body=body)], **kwargs) == []


def test_no_comments_passes() -> None:
    assert _unanswered([]) == []


def test_unanswered_finding_is_reported() -> None:
    result = _unanswered([_comment(1, BOT)])
    assert [r["url"] for r in result] == ["https://example.test/c/1"]
    assert result[0]["finding"] == "**HIGH** (Bug Hunter): finding 1"


def test_human_reply_answers_the_finding() -> None:
    assert _unanswered([_comment(1, BOT), _comment(2, "maintainer", reply_to=1)]) == []


@pytest.mark.parametrize(
    "body",
    [
        f"fixed in {SHA}",
        f"Fixed in {SHA[:7]}",
        f"fixed in {SHA.upper()}: moved the check before the write",
        "not a bug: the value is validated in parse() before it gets here",
        "Not a bug:\nthe caller holds the lock for the whole loop",
        f"deferred: {ISSUE}",
        f"Deferred: {ISSUE.upper().replace('HTTPS://GITHUB.COM', 'https://github.com')}",
        f"  deferred: {ISSUE} (needs a schema change)",
    ],
)
def test_contract_answers_count(body: str) -> None:
    assert _answered_by(body)


@pytest.mark.parametrize(
    "body",
    [
        "won't fix",
        "ok",
        "LGTM, this is fine",
        "not a bug: intended",  # a reason, but no reason given
        "not a bug:                          ",
        "fixed in abcdef0",  # not a commit on this PR
        "fixed in 0123",  # too short to name anything
        f"thanks, fixed in {SHA}",  # the form must lead
        "deferred: https://github.com/crunchtools/gatehouse/issues/91",  # closed or missing
        "deferred: later",
        f"deferred: {ISSUE.replace('issues', 'pull')}",
    ],
)
def test_other_replies_answer_nothing(body: str) -> None:
    """An agent can always reply, so a reply alone proves nothing."""
    assert not _answered_by(body)


def test_fixed_in_needs_a_commit_on_the_pr() -> None:
    assert not _answered_by(f"fixed in {SHA}", shas=())
    assert _answered_by(f"fixed in {SHA[:10]}", shas=("ffff", SHA))


def test_deferred_needs_the_issue_open() -> None:
    assert not _answered_by(f"deferred: {ISSUE}", open_issues=())


def test_one_contract_reply_among_chatter_answers() -> None:
    comments = [
        _comment(1, BOT),
        _comment(2, "maintainer", 1, body="looking"),
        _comment(3, "maintainer", 1, body=f"fixed in {SHA}"),
    ]
    assert _unanswered(comments) == []


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


def _review(rid: int, user: str, body: str) -> dict:
    return {
        "id": rid,
        "user": {"login": user},
        "body": body,
        "html_url": f"https://example.test/r/{rid}",
    }


CLEAN = f"Gatehouse found no issues.\n\n{REVIEW_MARKER}"
FAILED = f"Gatehouse could not review this PR: 0 of 8 agents finished.\n\n{REVIEW_FAILED_MARKER}"


def _failed_review(*pages: list[dict]) -> str:
    """Run triage.yml's review gate; the URL of the failed review holding the PR, or ""."""
    block = re.search(
        r"# --- review gate.*?\n(.*?)# --- end review gate ---", TRIAGE.read_text(), re.S
    )
    assert block, "review gate markers missing from triage.yml"
    program = re.search(r"jq -rs --arg bot .*? '(.*?)'", block.group(1), re.S)
    assert program, "jq program not found between the markers"
    out = subprocess.run(
        ["jq", "-rs", "--arg", "bot", BOT, program.group(1)],
        input="".join(json.dumps(page) for page in pages),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_review_gate_passes_with_no_reviews() -> None:
    assert _failed_review([]) == ""
    assert _failed_review() == ""


def test_review_gate_passes_on_a_clean_review() -> None:
    assert _failed_review([_review(1, BOT, CLEAN)]) == ""


def test_review_gate_holds_a_failed_review() -> None:
    """#77: a review in which nothing was reviewed fails triage."""
    assert _failed_review([_review(1, BOT, FAILED)]) == "https://example.test/r/1"


def test_review_gate_newest_review_decides() -> None:
    assert _failed_review([_review(1, BOT, FAILED)], [_review(2, BOT, CLEAN)]) == ""
    assert _failed_review([_review(1, BOT, CLEAN), _review(2, BOT, FAILED)]).endswith("/r/2")


def test_review_gate_ignores_other_authors_and_other_bot_reviews() -> None:
    reviews = [
        _review(1, BOT, CLEAN),
        _review(2, "mallory", FAILED),
        _review(3, BOT, f"Dependabot note quoting {REVIEW_FAILED_MARKER}"),
        {"id": 4, "user": {"login": BOT}, "body": None, "html_url": "https://example.test/r/4"},
    ]
    assert _failed_review(reviews) == ""


def test_review_workflow_posts_the_marker_triage_reads() -> None:
    """The marker is spelled in three places; they must agree."""
    assert REVIEW_FAILED_MARKER in TRIAGE.read_text()
    assert REVIEW_FAILED_MARKER in (TRIAGE.parent / "review.yml").read_text()
