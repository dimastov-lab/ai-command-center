"""Tests for the unified runs read layer (`command_center.runtime.runs_read`).

Covers the v2→v1.2 status vocabulary mapping, (source, run_id) identity, v2
report parsing, duration derivation, and the merge/dedup of the two sources —
the fix for the four pages that read only the empty v1.2 journal and showed
zero runs.
"""

from __future__ import annotations

from pathlib import Path

import command_center.runtime.runs_read as runs_read


def test_state_to_status_covers_all_run_states():
    from command_center.runtime.db import RUN_STATES
    # Every persisted v2 state must map to a v1.2 status the UI knows about.
    for state in RUN_STATES:
        assert state in runs_read._STATE_TO_STATUS, f"unmapped state: {state}"
        assert runs_read._STATE_TO_STATUS[state] in {
            "queued",
            "running",
            "completed",
            "failed",
            "timed_out",
            "cancelled",
        }


def test_agent_from_command_extracts_argv0():
    import json

    assert runs_read._agent_from_command(json.dumps(["claude", "-p", "x"])) == "claude"
    assert runs_read._agent_from_command(json.dumps(["/usr/local/bin/codex"])) == "codex"
    assert runs_read._agent_from_command(None) == "—"
    assert runs_read._agent_from_command("not-json") == "—"
    assert runs_read._agent_from_command(json.dumps([])) == "—"


def test_duration_seconds_computes():
    assert runs_read._duration_seconds(
        {"started_at": "2025-01-01T10:00:00", "completed_at": "2025-01-01T10:00:30"}
    ) == 30.0
    assert runs_read._duration_seconds({"started_at": None, "completed_at": None}) is None
    # completed before started (clock skew) -> None, not a negative duration.
    assert runs_read._duration_seconds(
        {"started_at": "2025-01-01T10:00:30", "completed_at": "2025-01-01T10:00:00"}
    ) is None


def test_normalize_v2_maps_state_and_status():
    run = runs_read._normalize_v2(
        {"id": "r1", "state": "FAILED", "project": "AICC", "task_type": "review",
         "created_at": "2025-01-01T10:00:00", "command_json": '["claude"]'},
        report=None,
        root=Path("."),
    )
    assert run["source"] == "v2"
    assert run["state"] == "FAILED"
    assert run["status"] == "failed"
    assert run["agent"] == "claude"
    assert run["parsed"]["verdict"] is None  # no report -> empty parsed shape


def test_normalize_v1_adds_source_and_state():
    run = runs_read._normalize_v1(
        {"id": "r2", "status": "completed", "project": "AICC", "parsed": {"verdict": "approved"}}
    )
    assert run["source"] == "v1.2"
    assert run["state"] == "COMPLETED"
    assert run["status"] == "completed"
    assert run["parsed"]["verdict"] == "approved"


def test_list_unified_runs_merges_and_dedups(tmp_path, monkeypatch):
    # Build a tiny v2 db with one run, and a v1.2 journal with two runs (one
    # sharing the v2 id -> must be deduped, one unique -> kept).
    from command_center.runtime import db

    db_path = tmp_path / "runtime.db"
    # Minimal schema: only what list_unified_runs touches (run + report tables).
    conn = __import__("sqlite3").connect(db_path)
    conn.executescript(
        """
        CREATE TABLE run (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, task_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, is_resume INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL, project TEXT NOT NULL, task_type TEXT NOT NULL,
            repository_path TEXT NOT NULL, prompt TEXT NOT NULL, command_json TEXT,
            timeout_seconds INTEGER, pid INTEGER, process_start_identity TEXT,
            pre_run_git_status TEXT, post_run_git_status TEXT,
            working_tree_changed INTEGER, exit_code INTEGER,
            cancel_requested INTEGER NOT NULL DEFAULT 0, cancel_requested_at TEXT,
            started_at TEXT, completed_at TEXT, version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE report (run_id TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    conn.execute(
        "INSERT INTO run (id, session_id, task_id, sequence, state, project, task_type, "
        "repository_path, prompt, created_at, updated_at) VALUES "
        "('shared','s','t',0,'COMPLETED','AICC','review','/r','p','2025-01-01T10:00:00','2025-01-01T10:00:00')"
    )
    conn.commit()
    conn.close()

    # Stub the v1.2 journal: one run sharing id 'shared' (dedup), one unique.
    monkeypatch.setattr(
        runs_read.agent_runner,
        "load_runs",
        lambda: [
            {"id": "shared", "status": "completed", "created_at": "2025-01-01T09:00:00"},
            {"id": "v1only", "status": "failed", "created_at": "2025-01-01T08:00:00", "parsed": {}},
        ],
    )

    merged = runs_read.list_unified_runs(db_path, root=tmp_path)
    ids = {(r["source"], r["id"]) for r in merged}
    assert ("v2", "shared") in ids          # v2 row wins over the v1.2 dup
    assert ("v1.2", "shared") not in ids    # v1.2 dup dropped
    assert ("v1.2", "v1only") in ids        # unique v1.2 run kept
    # Newest-first ordering.
    assert merged[0]["created_at"] >= merged[-1]["created_at"]