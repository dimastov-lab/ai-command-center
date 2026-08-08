"""Tests for scripts/run-sequence.py --dry-run and --status flags."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run-sequence.py"

# env var that run-sequence.py / tasks_repository reads for data dir override
DATA_DIR_ENV_VAR = "AICC_DATA_DIR"


@pytest.fixture
def fake_data_dir(tmp_path, monkeypatch):
    """Isolated data directory so tests never touch the real tasks.json."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tasks = [
        {"id": "AICC-D1-GATE", "launch_status": "Ready", "project": "aicc", "title": "Gate 1", "status": "Ready"},
        {"id": "AICC-D2A", "launch_status": "Ready", "project": "aicc", "title": "Test 2A", "status": "Ready"},
    ]
    (data_dir / "tasks.json").write_text(json.dumps(tasks, indent=2))
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(data_dir))
    return data_dir


def test_dry_run_prints_statuses(fake_data_dir):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "AICC-D1-GATE", "AICC-D2A"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, DATA_DIR_ENV_VAR: str(fake_data_dir)},
    )
    assert result.returncode == 0
    assert "DRYRUN" in result.stdout or "DRY-RUN" in result.stdout


def test_dry_run_does_not_launch(fake_data_dir):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "AICC-D1-GATE", "AICC-D2A"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, DATA_DIR_ENV_VAR: str(fake_data_dir)},
    )
    assert "Launching" not in result.stdout


def test_invalid_task_id_fails(fake_data_dir):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "NONEXISTENT-TASK"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, DATA_DIR_ENV_VAR: str(fake_data_dir)},
    )
    assert result.returncode != 0
    assert "do not exist" in result.stderr or "Error" in result.stderr


def test_status_no_sequence(fake_data_dir):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--status"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, DATA_DIR_ENV_VAR: str(fake_data_dir)},
    )
    assert result.returncode == 0
    assert "No sequence in progress" in result.stdout


def test_status_with_active_sequence(fake_data_dir):
    # Create a state file with an active sequence
    state_file = fake_data_dir / "sequence_state.json"
    state = {
        "task_ids": ["AICC-D1-GATE", "AICC-D2A"],
        "current_index": 1,
        "steps": {
            "AICC-D1-GATE": {"status": "Completed", "skipped": False},
        },
    }
    state_file.write_text(json.dumps(state, indent=2))

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--status"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, DATA_DIR_ENV_VAR: str(fake_data_dir)},
    )
    assert result.returncode == 0
    assert "Sequence Progress: 1/2" in result.stdout
    assert "AICC-D1-GATE" in result.stdout
    assert "AICC-D2A" in result.stdout
    assert "completed" in result.stdout
    assert "in progress" in result.stdout


def test_status_with_completed_sequence(fake_data_dir):
    # Create a state file with a completed sequence
    state_file = fake_data_dir / "sequence_state.json"
    state = {
        "task_ids": ["AICC-D1-GATE", "AICC-D2A"],
        "current_index": 2,
        "steps": {
            "AICC-D1-GATE": {"status": "Completed", "skipped": False},
            "AICC-D2A": {"status": "Completed", "skipped": False},
        },
    }
    state_file.write_text(json.dumps(state, indent=2))

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--status"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, DATA_DIR_ENV_VAR: str(fake_data_dir)},
    )
    assert result.returncode == 0
    assert "Sequence Progress: 2/2" in result.stdout
    assert "AICC-D1-GATE" in result.stdout
    assert "AICC-D2A" in result.stdout
