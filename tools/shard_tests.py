#!/usr/bin/env python3
"""Assign test files to CI shards by measured duration, not by count.

**Ported from the platform repository (`aios`, `tools/shard_tests.py`), where it
was written, reviewed and measured.** It is copied rather than reimplemented
because the reasoning below is the expensive part and it is already paid for;
the numbers quoted are the platform's, and this repository's own baseline is
recorded in the commit that introduces the sharded job. Nothing here is
platform-specific: it takes a directory of test files, a duration history and a
shard count.

The problem it solves is the same in both repositories. Sharding with
`i % shards` over a sorted file list balances the *number* of files, which is
not what anyone waits for: run wall time is the **slowest** shard, and a file's
cost varies by two orders of magnitude. In the platform, measured over four
successful runs, the imbalance was 1.57-1.78 against a 1.20 target, and the
critical path was the same shard every time — deterministic, because a given
file always lands in the same place.

This repository starts from something worse than an unbalanced split: one job
running the whole suite, so the wall time is the *sum* rather than the maximum.

This assigns by longest-processing-time: sort files by known duration descending,
and repeatedly give the next file to whichever shard is currently cheapest. LPT
is the standard greedy for this and is within 4/3 of optimal, which is far inside
the gap being closed.

Two properties matter more than the packing quality:

*Determinism.* Ties break on the file path, so the same inputs always produce the
same assignment -- a scheduler that shuffled work between runs would make a
flaky failure impossible to reproduce on the shard that produced it.

*No silent loss.* Every discovered file is assigned to exactly one shard whether
or not history knows its duration; unknown files fall back to the median known
duration so they are spread rather than piled onto shard 0. A scheduler that
dropped an uncovered file would trade wall time for coverage, and the drop would
look exactly like a green run.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATIONS_PATH = ROOT / "tests" / "durations.json"


def discover(tests_dir: Path) -> list[str]:
    """Every test module, as repo-relative paths, in a stable order.

    `tests_dir` is resolved first: called with a relative path (the natural
    thing from a shell) an unresolved version raises `ValueError` from
    `relative_to`, which in CI would surface as an empty shard rather than as
    an obvious error.
    """
    resolved = tests_dir.resolve()
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in resolved.rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


def load_durations(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Unreadable history is missing history: fall back rather than fail the
        # run. The fallback is correct, only slower.
        return {}
    durations = raw.get("durations", raw)
    if not isinstance(durations, dict):
        return {}
    return {
        str(name): float(value)
        for name, value in durations.items()
        if isinstance(value, (int, float)) and float(value) >= 0
    }


def assign(files: list[str], durations: dict[str, float], shards: int) -> list[list[str]]:
    """Longest-processing-time assignment of `files` across `shards`."""
    if shards < 1:
        raise ValueError("shards must be >= 1")
    known = [durations[name] for name in files if name in durations]
    # An unknown file is assumed typical, not free. Assuming zero would pile
    # every new test onto one shard until history caught up.
    default = statistics.median(known) if known else 1.0

    ordered = sorted(files, key=lambda name: (-durations.get(name, default), name))
    buckets: list[list[str]] = [[] for _ in range(shards)]
    totals = [0.0] * shards
    for name in ordered:
        target = min(range(shards), key=lambda index: (totals[index], index))
        buckets[target].append(name)
        totals[target] += durations.get(name, default)
    return [sorted(bucket) for bucket in buckets]


def _costs(files: list[str], durations: dict[str, float]) -> dict[str, float]:
    known = [durations[name] for name in files if name in durations]
    default = statistics.median(known) if known else 1.0
    return {name: durations.get(name, default) for name in files}


def imbalance(files: list[str], durations: dict[str, float], shards: int) -> float:
    """max(shard)/mean(shard) for the assignment, by known duration."""
    cost = _costs(files, durations)
    totals = [
        sum(cost[name] for name in bucket) for bucket in assign(files, durations, shards)
    ]
    mean = statistics.mean(totals)
    return max(totals) / mean if mean else 1.0


def theoretical_floor(files: list[str], durations: dict[str, float], shards: int) -> float:
    """The best imbalance any scheduler could reach for this suite.

    A file cannot be split across shards, so the slowest shard is at least as
    slow as the single slowest file. Where one file costs more than its share,
    no assignment reaches the 1.20 target and reporting 1.20 as achievable would
    be measuring the scheduler against an impossible bar -- the honest response
    is to say the target is unreachable and why, not to tune until the number
    looks right.
    """
    cost = _costs(files, durations)
    if not cost:
        return 1.0
    mean = sum(cost.values()) / shards
    return max(cost.values()) / mean if mean else 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True, help="1-based shard index")
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--tests-dir", type=Path, default=ROOT / "tests")
    parser.add_argument("--durations", type=Path, default=DURATIONS_PATH)
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the projected imbalance instead of a file list",
    )
    args = parser.parse_args()

    if not 1 <= args.shard <= args.shards:
        parser.error("--shard must be between 1 and --shards")

    files = discover(args.tests_dir)
    durations = load_durations(args.durations)

    if args.report:
        covered = sum(1 for name in files if name in durations)
        print(f"files={len(files)} with_history={covered} shards={args.shards}")
        print(f"projected_imbalance={imbalance(files, durations, args.shards):.3f}")
        return 0

    for name in assign(files, durations, args.shards)[args.shard - 1]:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
