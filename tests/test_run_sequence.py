"""Tests for scripts/run-sequence.py --dry-run flag."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from command_center import storage

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run-sequence.py"


@pytest.fixture
def test_tasks(tmp_path):
    """Create test tasks in the temporary data directory."""
    data_dir = storage.resolve_data_dir(Path.cwd())
    tasks_file = data_dir / "tasks.json"
    test_tasks_list = [
        {"id": "AICC-D1-GATE", "launch_status": "Ready", "project": "aicc", "title": "Gate 1", "status": "Ready"},
        {"id": "AICC-D2A", "launch_status": "Ready", "project": "aicc", "title": "Test 2A", "status": "Ready"},
    ]
    tasks_file.write_text(json.dumps(test_tasks_list, indent=2))
    return test_tasks_list


def test_dry_run_prints_statuses(test_tasks):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "AICC-D1-GATE", "AICC-D2A"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DRYRUN" in result.stdout or "DRY-RUN" in result.stdout


def test_dry_run_does_not_launch(test_tasks):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "AICC-D1-GATE", "AICC-D2A"],
        capture_output=True,
        text=True,
    )
    assert "Launching" not in result.stdout


def test_invalid_task_id_fails(test_tasks):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "NONEXISTENT-TASK"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "do not exist" in result.stderr or "Error" in result.stderr
