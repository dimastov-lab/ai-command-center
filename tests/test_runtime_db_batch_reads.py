"""Batch read helpers that collapse the Execution Center's per-run N+1 into one
query each (audit H5): completions, reports, and the latest event per run. Each
batch result must equal what the single-row read returns, and an empty run-id
list must be a no-op (no query, empty dict).
"""
from __future__ import annotations

from command_center.runtime import db, log_tail


def _run(db_path, workspace):
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path=workspace)
    run = db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AIOS",
        task_type="implementation", repository_path=workspace, prompt="p", is_resume=False,
    )
    return task, run


def test_get_completions_for_runs_matches_single_row_reads(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    t1, r1 = _run(db_path, "/tmp/a")
    t2, r2 = _run(db_path, "/tmp/b")
    _t3, r3 = _run(db_path, "/tmp/c")  # no completion
    db.create_completion(db_path, run_id=r1["id"], task_id=t1["id"], project="AIOS",
                         repository_path="/tmp/a", completion_state="AWAITING_REVIEW")
    db.create_completion(db_path, run_id=r2["id"], task_id=t2["id"], project="AIOS",
                         repository_path="/tmp/b", completion_state="MERGED")

    batch = db.get_completions_for_runs(db_path, [r1["id"], r2["id"], r3["id"]])
    assert set(batch) == {r1["id"], r2["id"]}  # r3 absent, not None
    assert batch[r1["id"]] == db.get_completion(db_path, r1["id"])
    assert batch[r2["id"]]["completion_state"] == "MERGED"


def test_get_reports_for_runs_matches_single_row_reads(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    _t1, r1 = _run(db_path, "/tmp/a")
    _t2, r2 = _run(db_path, "/tmp/b")  # no report
    db.create_report(db_path, r1["id"], "/tmp/a/report.md")

    batch = db.get_reports_for_runs(db_path, [r1["id"], r2["id"]])
    assert set(batch) == {r1["id"]}
    assert batch[r1["id"]] == db.get_report(db_path, r1["id"])


def test_latest_events_for_runs_returns_the_most_recent_per_run(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    _t1, r1 = _run(db_path, "/tmp/a")
    _t2, r2 = _run(db_path, "/tmp/b")
    _t3, r3 = _run(db_path, "/tmp/c")  # no events
    for i in range(3):
        db.append_run_event(db_path, r1["id"], "lifecycle", {"i": i})
    db.append_run_event(db_path, r2["id"], "lifecycle", {"only": True})

    batch = db.latest_events_for_runs(db_path, [r1["id"], r2["id"], r3["id"]])
    assert set(batch) == {r1["id"], r2["id"]}  # r3 has no events
    assert batch[r1["id"]]["payload"] == {"i": 2}  # the last appended, not an earlier one
    assert batch[r1["id"]] == log_tail.latest_event(db_path, r1["id"])
    assert batch[r2["id"]]["payload"] == {"only": True}


def test_batch_reads_are_a_noop_on_empty_input(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    assert db.get_completions_for_runs(db_path, []) == {}
    assert db.get_reports_for_runs(db_path, []) == {}
    assert db.latest_events_for_runs(db_path, []) == {}
