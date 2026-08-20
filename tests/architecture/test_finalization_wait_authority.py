"""VOYN-W0-AICC-FLAKE-03: one test-side answer to "is this run finished?".

`Supervisor._supervise` publishes the terminal run row before it appends
`process_exited`, auto-commits the agent's work and saves the report, on a
daemon thread nothing joins. Any test that launches a real run has to wait out
that window, and for a while every module that needed to had written its own
wait — five of them, none importing the others, each a slightly different and
weaker predicate than `run.finalized_at`.

Duplicate authority is what makes that class of bug survive its own fix: the
next module to need a wait copies whichever neighbour it happens to read, and
the one that learns something (that `INTERRUPTED` produces no report, say)
teaches only itself. So the shape is checked, not just corrected once —
`tests/finalization_helpers.wait_for_finalized_run` is the only place in the
suite allowed to loop on the question.

The check is structural rather than name-based, because renaming
`_wait_for_report` was never the hard part: it looks for the *predicate* — a
loop that polls the report row or the finalization marker — wherever it appears.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]

#: The two files that must poll raw, with the reason each is not a duplicate of
#: the helper:
#:
#: - `finalization_helpers.py` *is* the authority.
#: - `fixtures/finalization_kill_probe.py` does the opposite of waiting: one of
#:   its loops SIGKILLs the process while the run is terminal-and-unfinalized
#:   (the window itself), and the other deliberately observes from a process
#:   that does not own the run, which is the property under test there.
_ALLOWED = frozenset(
    {
        Path("finalization_helpers.py"),
        Path("fixtures/finalization_kill_probe.py"),
    }
)

#: Reading the report row, or the marker, *inside a loop* is the hand-rolled
#: wait. A single read of either is an assertion, not a wait, and stays fine.
_POLLED_CALLS = frozenset({"get_report"})
_POLLED_FIELDS = frozenset({"finalized_at"})


def _hand_rolled_waits(tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in _POLLED_CALLS:
                    hits.append(inner.lineno)
            elif isinstance(inner, ast.Constant) and inner.value in _POLLED_FIELDS:
                hits.append(inner.lineno)
            elif isinstance(inner, ast.Attribute) and inner.attr in _POLLED_FIELDS:
                hits.append(inner.lineno)
    return sorted(set(hits))


def test_only_the_shared_helper_waits_for_a_run_to_finalize():
    offenders: dict[str, list[int]] = {}
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        relative = path.relative_to(TESTS_ROOT)
        if relative in _ALLOWED or "__pycache__" in relative.parts:
            continue
        hits = _hand_rolled_waits(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            offenders[str(relative)] = hits

    assert not offenders, (
        "these modules poll for run finalization themselves instead of calling "
        "`tests.finalization_helpers.wait_for_finalized_run` — the duplicate "
        "authority VOYN-W0-AICC-FLAKE-03 consolidated away "
        f"(file -> line numbers): {offenders}"
    )


def test_the_gate_can_actually_see_a_hand_rolled_wait():
    """The gate's own mutation, kept in the file it guards.

    A structural check that matches nothing passes for free, which is how the
    previous fixture in this family (`widen_finalization`) shipped green while
    measuring nothing. This is the exact shape that was deleted from five
    modules; if the walker stops recognising it, this fails first.
    """
    retired = ast.parse(
        "def _wait_for_report(db_path, run_id, *, timeout=10.0):\n"
        "    deadline = time.monotonic() + timeout\n"
        "    while time.monotonic() < deadline:\n"
        "        if runtime_db.get_report(db_path, run_id) is not None:\n"
        "            return\n"
        "        time.sleep(0.05)\n"
    )
    assert _hand_rolled_waits(retired) == [4]

    marker_flavoured = ast.parse(
        "while True:\n"
        "    row = db.get_run(path, run_id)\n"
        '    if row and row["finalized_at"]:\n'
        "        break\n"
    )
    assert _hand_rolled_waits(marker_flavoured) == [3]

    single_read = ast.parse('assert runtime_db.get_report(db_path, run_id) is not None\n')
    assert _hand_rolled_waits(single_read) == []
