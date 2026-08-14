#!/usr/bin/env python3
"""Measure the evidence line instead of remembering it.

Every rejection this repository's PostgreSQL migration produced — six of them
across fifteen slices — was the same defect, and none was a defect in the code:
a sentence claiming more than had been run. Four were test counts written from
memory after a rebase changed them; the others were behavioural claims
("the contract checks X", "each write is covered") that nothing had exercised.

A number a human types is a number that can drift. This tool types it instead:

    uv run python scripts/evidence.py measure tests/db
    -> Evidence: `tests/db` 609 passed / 44 skipped; ruff clean.

and, more usefully, re-measures a claim that already exists so the drift is
found before a reviewer finds it:

    uv run python scripts/evidence.py check --message-from-head tests/db
    -> claims 527 passed / 40 skipped, measured 609 passed / 44 skipped  [MISMATCH]

`check` exits non-zero on a mismatch, so it can gate a push — and it fails
**closed**: a red suite, or a message whose claim it cannot parse, is also a
non-zero exit. The first version returned 0 in both cases, which independent
acceptance demonstrated in three shapes; a gate that passes when it cannot tell
is not a gate, and it is the exact defect class this tool was written to end.

It deliberately does **not** rewrite the message: a wrong number is often the
visible end of a wrong belief, and silently correcting it would hide the part
worth reading.

Deliberately not a pytest plugin and not part of the required suite. This
measures what a *claim* says against what a run says; a suite that measured its
own claims would be the thing being checked.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: `123 passed, 4 skipped` / `123 passed` / `1 failed, 2 passed` — pytest's own
#: summary line, whatever order it puts the terms in.
_SUMMARY = re.compile(r"(?P<count>\d+) (?P<outcome>passed|failed|skipped|errors?|xfailed)")

#: How an evidence sentence in this repository states a measurement. Anchored
#: to the `Evidence:` line rather than matching anywhere in the text: a message
#: that *discusses* counts — a fenced example, a quoted failure, a correction
#: describing what a number used to be — would otherwise be compared against
#: one measured suite and report mismatches that are not claims at all. A gate
#: that always fires is a gate people learn to skip.
_CLAIM = re.compile(
    r"^Evidence:[^\n]*?(?P<passed>\d+)\s+passed\s*[/,]\s*(?P<skipped>\d+)\s+skipped",
    re.MULTILINE,
)



def _pytest_command(paths: list[str], extra: list[str]) -> list[str]:
    """How to invoke pytest here, rather than how I invoke it on my laptop.

    The first version hard-coded `uv run --with …`, which works locally and
    does not exist on a CI runner that installs its dependencies with pip. The
    tool then produced no output at all and its own tests failed on empty
    stdout — a gate that only runs in one environment is not a gate, and this
    one had shipped claiming it could gate a push.

    `uv` is preferred when present because it supplies the optional database
    extras this repository's PostgreSQL tests want; otherwise the interpreter
    already running is exactly the right one.
    """
    if shutil.which("uv"):
        return ["uv", "run", "--with", "psycopg-pool>=3.2,<4", "pytest", *paths, "-q", *extra]
    return [sys.executable, "-m", "pytest", *paths, "-q", *extra]


def _run_pytest(paths: list[str], extra: list[str]) -> dict[str, int]:
    """Run pytest and return its own counts, parsed from its own summary.

    The counts come from pytest rather than from a re-implementation of
    pytest's bookkeeping: a second definition of "how many tests passed" is a
    second thing to be wrong.
    """
    completed = subprocess.run(
        _pytest_command(paths, extra), cwd=ROOT, capture_output=True, text=True
    )
    tail = [line for line in completed.stdout.splitlines() if _SUMMARY.search(line)]
    if not tail:
        sys.stderr.write(completed.stdout[-4000:])
        raise SystemExit(f"pytest produced no summary line for {paths}")
    counts = {
        match.group("outcome"): int(match.group("count"))
        for match in _SUMMARY.finditer(tail[-1])
    }
    counts.setdefault("passed", 0)
    counts.setdefault("skipped", 0)
    counts["_returncode"] = completed.returncode
    return counts


def _ruff() -> str:
    """`clean`, `dirty`, or `unavailable` — three answers, not two.

    Collapsing the third into "dirty" made the tool report a lint problem
    where there was only a missing lint tool, and then refuse to exit 0 over
    it. Fail-closed is right for a claim about tests; it is wrong for an
    inability to check something the claim does not assert, and a gate that
    cries about the wrong thing is one people route around.
    """
    ruff = ["uv", "run", "ruff"] if shutil.which("uv") else [sys.executable, "-m", "ruff"]
    try:
        completed = subprocess.run(
            [*ruff, "check", "."], cwd=ROOT, capture_output=True, text=True
        )
    except OSError:
        return "unavailable"
    if completed.returncode == 0:
        return "clean"
    if "No module named" in completed.stderr or "error: unrecognized" in completed.stderr:
        return "unavailable"
    return "dirty"


def _evidence_line(paths: list[str], counts: dict[str, int], ruff_state: str) -> str:
    scope = ", ".join(f"`{path}`" for path in paths)
    failed = counts.get("failed", 0) + counts.get("errors", 0) + counts.get("error", 0)
    parts = [f"{counts['passed']} passed"]
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped")
    if failed:
        # Stated, never omitted. An evidence line that quietly drops failures
        # is worse than no evidence line, because it reads like a clean run.
        parts.append(f"**{failed} FAILED**")
    suffix = {
        "clean": "; ruff clean.",
        "dirty": "; **ruff NOT clean**.",
        "unavailable": "; ruff not run here.",
    }[ruff_state]
    return f"Evidence: {scope} " + " / ".join(parts) + suffix


def _head_message() -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    measure = sub.add_parser("measure", help="run the suite and print the evidence line")
    measure.add_argument("paths", nargs="+")

    check = sub.add_parser("check", help="re-measure the claims already in a message")
    check.add_argument("paths", nargs="+")
    source = check.add_mutually_exclusive_group(required=True)
    source.add_argument("--message-from-head", action="store_true")
    source.add_argument("--message-file", type=Path)

    args = parser.parse_args(argv)

    if "AICC_TEST_PG_ADMIN_DSN" not in os.environ:
        # Warned rather than refused: the serverless configuration is a real
        # one and the counts differ there legitimately. What must not happen is
        # a number measured in one configuration being read as the other.
        sys.stderr.write(
            "warning: AICC_TEST_PG_ADMIN_DSN is unset — this measures the "
            "serverless configuration, where PostgreSQL-backed tests skip.\n"
        )

    counts = _run_pytest(args.paths, [])
    ruff_state = _ruff()
    line = _evidence_line(args.paths, counts, ruff_state)
    # A failing suite is a non-zero exit whatever the claim says: the first
    # version compared only the numbers, so a red suite whose `passed`/`skipped`
    # happened to match its claim exited 0 and reported `[ok]` — the tool
    # agreeing with a sentence while the thing it describes was on fire.
    #
    # A dirty `ruff` is reported in the line but does not decide the exit code.
    # This tool answers one question — does the claimed measurement match the
    # measurement — and the repository already has a dedicated lint gate that
    # answers the other one.
    healthy = counts["_returncode"] == 0

    if args.command == "measure":
        print(line)
        return 0 if healthy else 1

    message = (
        _head_message()
        if args.message_from_head
        else args.message_file.read_text(encoding="utf-8")
    )
    claims = list(_CLAIM.finditer(message))
    if not claims:
        # Non-zero, because "I could not find the claim" is not "the claim is
        # right". A message with no `Evidence:` line has nothing for a reviewer
        # to check either, which is itself worth stopping for.
        print(f"no `Evidence: ... N passed / M skipped` line found in the message\n{line}")
        return 1

    mismatched = False
    for claim in claims:
        claimed = (int(claim.group("passed")), int(claim.group("skipped")))
        measured = (counts["passed"], counts["skipped"])
        verdict = "ok" if claimed == measured else "MISMATCH"
        mismatched |= claimed != measured
        print(
            f"claims {claimed[0]} passed / {claimed[1]} skipped, "
            f"measured {measured[0]} passed / {measured[1]} skipped  [{verdict}]"
        )
    print(line)
    return 1 if (mismatched or not healthy) else 0


if __name__ == "__main__":
    raise SystemExit(main())
