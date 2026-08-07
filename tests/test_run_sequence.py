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
