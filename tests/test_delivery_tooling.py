"""The gates that check other work, checked themselves.

Both tools in this file's scope shipped without a test, and independent
acceptance immediately found each of them reporting success it had not
measured: the sweep called every hook "caught" against an already-red suite,
and the evidence check exited 0 over a failing run and over a message whose
claim it could not parse. Tooling whose whole purpose is to refuse a false
claim is the last place to accept one, so every attack that worked is a test
here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "scripts" / "evidence.py"
SLICE_CHECKS = ROOT / "scripts" / "mirror_slice_checks.py"


def _write_suite(directory: Path, *, green: bool) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    body = "def test_ok():\n    assert True\n" if green else (
        "def test_ok():\n    assert True\n\n\ndef test_broken():\n    assert False, 'on fire'\n"
    )
    path = directory / "test_probe.py"
    path.write_text(body, encoding="utf-8")
    return path


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=ROOT, capture_output=True, text=True
    )


def test_evidence_check_fails_on_a_red_suite_even_when_the_numbers_agree(tmp_path) -> None:
    """The shape acceptance used: a claim that matches, over a suite that fails.

    `1 passed / 0 skipped` is literally true of the run below, and the run also
    contains a failure. Comparing only the two numbers made the tool agree with
    a sentence while the thing it describes was broken.
    """
    suite = _write_suite(tmp_path / "red", green=False)
    message = tmp_path / "message.txt"
    message.write_text(
        f"fix: something\n\nEvidence: `{suite}` 1 passed / 0 skipped; ruff clean.\n",
        encoding="utf-8",
    )

    result = _run(EVIDENCE, "check", "--message-file", str(message), str(suite))
    assert result.returncode != 0, result.stdout
    assert "FAILED" in result.stdout


def test_evidence_check_fails_when_it_cannot_find_the_claim(tmp_path) -> None:
    """"I could not find the claim" is not "the claim is right".

    A message with no `Evidence:` line gives a reviewer nothing to check
    either, so stopping is the useful answer rather than a silent zero.
    """
    suite = _write_suite(tmp_path / "green", green=True)
    message = tmp_path / "message.txt"
    message.write_text("fix: something\n\nno numbers here at all\n", encoding="utf-8")

    result = _run(EVIDENCE, "check", "--message-file", str(message), str(suite))
    assert result.returncode != 0
    assert "no `Evidence:" in result.stdout


def test_evidence_check_ignores_counts_that_are_not_the_evidence_line(tmp_path) -> None:
    """A message may legitimately discuss other counts.

    Corrections quote what a number used to be, and examples show what a
    mismatch looks like. Matching those against the measured suite made the
    tool fire on messages that were entirely honest — and a gate that always
    fires is one people learn to skip.
    """
    suite = _write_suite(tmp_path / "green", green=True)
    message = tmp_path / "message.txt"
    message.write_text(
        "fix: something\n\n"
        "The previous message said 527 passed / 40 skipped, which was stale.\n\n"
        f"Evidence: `{suite}` 1 passed / 0 skipped; ruff clean.\n",
        encoding="utf-8",
    )

    result = _run(EVIDENCE, "check", "--message-file", str(message), str(suite))
    assert result.returncode == 0, result.stdout
    assert result.stdout.count("[ok]") == 1


def test_evidence_check_reports_a_drifted_claim(tmp_path) -> None:
    suite = _write_suite(tmp_path / "green", green=True)
    message = tmp_path / "message.txt"
    message.write_text(
        f"fix: something\n\nEvidence: `{suite}` 99 passed / 3 skipped; ruff clean.\n",
        encoding="utf-8",
    )

    result = _run(EVIDENCE, "check", "--message-file", str(message), str(suite))
    assert result.returncode != 0
    assert "MISMATCH" in result.stdout


def test_the_sweep_refuses_to_run_against_a_red_suite(tmp_path) -> None:
    """The finding that mattered most, because it inverted the tool's meaning.

    With no baseline, `caught = returncode != 0` cannot distinguish "the
    perturbation was noticed" from "the suite was already failing", so a red
    suite reported every hook as covered — the tool announcing the coverage it
    was built to doubt.
    """
    suite = _write_suite(tmp_path / "red", green=False)

    result = _run(
        SLICE_CHECKS,
        "sweep",
        "command_center/runtime/db/completion.py",
        "--suite",
        str(suite),
    )
    assert result.returncode != 0, result.stdout
    assert "already failing" in result.stdout
    assert "caught" not in result.stdout, "a red baseline must never produce a coverage verdict"


def test_the_sweep_leaves_the_tree_untouched(tmp_path) -> None:
    """Including when it refuses: a probe that can leave the tree perturbed
    will one day be blamed for a real failure."""
    suite = _write_suite(tmp_path / "red", green=False)
    target = ROOT / "command_center/runtime/db/completion.py"
    before = target.read_text(encoding="utf-8")

    _run(SLICE_CHECKS, "sweep", "command_center/runtime/db/completion.py", "--suite", str(suite))

    assert target.read_text(encoding="utf-8") == before
