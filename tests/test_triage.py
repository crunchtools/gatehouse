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


def _comment(cid: int, user: str, reply_to: int | None = None) -> dict:
    return {
        "id": cid,
        "user": {"login": user},
        "in_reply_to_id": reply_to,
        "html_url": f"https://example.test/c/{cid}",
        "path": "src/app.py",
        "line": 10,
        "original_line": 10,
        "body": f"- **HIGH** (Bug Hunter): finding {cid}\nmore",
    }


def _unanswered(*pages: list[dict]) -> list[dict]:
    block = re.search(
        r"# --- triage filter.*?\n(.*?)# --- end triage filter ---",
        TRIAGE.read_text(),
        re.S,
    )
    assert block, "triage filter markers missing from triage.yml"
    program = re.search(r"jq -s --arg bot \"\$REVIEWER\" '(.*?)'", block.group(1), re.S)
    assert program, "jq program not found between the markers"
    # gh api --paginate emits one JSON array per page; -s slurps them.
    stdin = "".join(json.dumps(page) for page in pages)
    out = subprocess.run(
        ["jq", "-s", "--arg", "bot", BOT, program.group(1)],
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
    assert result[0]["finding"] == "- **HIGH** (Bug Hunter): finding 1"


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
    assert text.count("if: github.event_name == 'pull_request_target'") == 2


def test_example_guard_gates_on_head_repo_not_author_role() -> None:
    """A fork is what pull_request_target guards against; author role is not.

    author_association trusted a collaborator pushing from their own fork and
    distrusted Dependabot, whose branches live in the repo.
    """
    guard = EXAMPLE.read_text().split("  review:")[0]
    assert '[ "$HEAD_REPO" = "$GITHUB_REPOSITORY" ]' in guard
    assert "head.repo.full_name" in guard
    assert "author_association" not in guard
