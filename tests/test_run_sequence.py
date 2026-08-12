"""Tests for scripts/run-sequence.py --dry-run flag."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run-sequence.py"


def test_dry_run_prints_statuses():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "FAKE-001", "FAKE-002"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DRYRUN" in result.stdout or "DRY-RUN" in result.stdout


def test_dry_run_does_not_launch():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "FAKE-001", "FAKE-002"],
        capture_output=True,
        text=True,
    )
    assert "Launching" not in result.stdout


def test_unknown_task_ids_refuse_to_start():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "NOPE-404"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Unknown task IDs" in result.stderr
    assert "Launching" not in result.stdout


def test_status_flag_reports_no_sequence_without_state():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--status"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "No sequence in progress" in result.stdout or "Sequence:" in result.stdout
