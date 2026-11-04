"""Measure how stable the reviewer is: the same diff, reviewed several times.

Consensus is supposed to make a review repeatable. This is how we know whether
it does, rather than asserting it: every diff in the corpus is reviewed a few
times and the finding sets are compared by Jaccard index, 1.0 meaning two runs
found exactly the same things.

    uv run python scripts/stability_eval.py --dry-run     # no model calls
    uv run python scripts/stability_eval.py --runs 3
    uv run python scripts/stability_eval.py --corpus other/diffs --json out.json
    uv run python scripts/stability_eval.py --samples 1   # the pre-consensus baseline

It calls a live model, so it costs money and its result is a measurement, not a
verdict. It cannot be a required check: a number that moves with model sampling
must not gate a merge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path

from gatehouse.cli import ENV_FILE, load_api_key, load_env_file
from gatehouse.review import run_review

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = REPO_ROOT / "tests" / "corpus"
NAME_WIDTH = 40


@dataclass(frozen=True)
class Measurement:
    """How repeatable one diff's review was across several runs."""

    diff: str
    runs: int
    counts: list[int]
    stability: float
    always: list[str]
    sometimes: list[str]

    def row(self) -> str:
        """One line of the report table."""
        name = self.diff[:NAME_WIDTH].ljust(NAME_WIDTH)
        counts = ",".join(str(count) for count in self.counts)
        return f"{name} {self.runs:>4}  {counts:>8}  {self.stability:.2f}"


async def finding_ids(diff: str, api_key: str, samples: int | None) -> set[str]:
    """The identities of every finding one review of this diff reported."""
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "consensus.json"
        await run_review(
            stdin_diff=diff,
            advisory=True,
            api_key=api_key,
            samples=samples,
            consensus_json=str(out),
        )
        if not out.exists():
            return set()
        return {record["id"] for record in json.loads(out.read_text())["findings"]}


def jaccard(one: set[str], other: set[str]) -> float:
    """How much two runs agreed. Two empty runs agreed completely."""
    union = one | other
    if not union:
        return 1.0
    return len(one & other) / len(union)


def pairwise(runs: list[set[str]]) -> list[float]:
    """The Jaccard index of every pair of runs."""
    return [jaccard(one, other) for one, other in combinations(runs, 2)]


def report(results: list[Measurement]) -> str:
    """A table of per-diff stability, with the mean across the corpus."""
    header = "diff".ljust(NAME_WIDTH) + " runs  findings  stability"
    lines = [header, *(result.row() for result in results)]
    if results:
        mean = statistics.mean(result.stability for result in results)
        lines.append(f"\nmean stability across {len(results)} diff(s): {mean:.2f}")
    return "\n".join(lines)


async def measure(
    diffs: list[Path], runs: int, samples: int | None, api_key: str
) -> list[Measurement]:
    """Review every diff ``runs`` times and score how much the runs agreed."""
    results: list[Measurement] = []
    for path in diffs:
        seen = [await finding_ids(path.read_text(), api_key, samples) for _ in range(runs)]
        scores = pairwise(seen)
        always = set.intersection(*seen)
        results.append(
            Measurement(
                diff=path.name,
                runs=runs,
                counts=[len(run) for run in seen],
                stability=statistics.mean(scores) if scores else 1.0,
                always=sorted(always),
                sometimes=sorted(set.union(*seen) - always),
            )
        )
        print(f"{path.name}: {results[-1].stability:.2f}", file=sys.stderr)
    return results


def parse_args() -> argparse.Namespace:
    """The command line."""
    parser = argparse.ArgumentParser(prog="stability_eval", description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="Directory of .diff files")
    parser.add_argument("--runs", type=int, default=3, help="Reviews per diff (default: 3)")
    parser.add_argument(
        "--samples", type=int, default=None, help="Override the per-agent sample count"
    )
    parser.add_argument("--json", default=None, metavar="PATH", help="Write the results as JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check the corpus and print the plan without calling a model",
    )
    return parser.parse_args()


async def main() -> int:
    """Measure the corpus and print the table; 2 on a usage error."""
    args = parse_args()
    diffs = sorted(Path(args.corpus).glob("*.diff"))
    if not diffs:
        print(f"No .diff files in {args.corpus}", file=sys.stderr)
        return 2
    if args.runs < 2:
        print("--runs must be at least 2: stability is a comparison", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"{len(diffs)} diff(s) x {args.runs} runs = {len(diffs) * args.runs} reviews")
        for path in diffs:
            print(f"  {path.name} ({len(path.read_text().splitlines())} lines)")
        return 0

    load_env_file(ENV_FILE)
    api_key = load_api_key()
    if not api_key:
        print(f"Error: OPENROUTER_API_KEY not set (or in {ENV_FILE})", file=sys.stderr)
        return 2

    results = await measure(diffs, args.runs, args.samples, api_key)
    print(report(results))
    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(result) for result in results], indent=2) + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
