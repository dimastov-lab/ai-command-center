import subprocess

from command_center import report_parser
from command_center.runtime import api as runtime_api
from command_center.runtime import db, task_sync


def _make_task(**overrides) -> dict:
    task = {
        "id": "task-1",
        "project": "AIOS",
        "title": "Deploy P1",
        "launch_status": "Ready",
        "current_run_id": None,
        "progress": 0,
        "progress_mode": "auto",
        "timeline": [],
    }
    task.update(overrides)
    return task


def _make_run(db_path, *, state: str, task_id: str = "task-1", **fields) -> dict:
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation", task_id=task_id)
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )
    if state != "PREPARED":
        run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    if state not in ("PREPARED", "QUEUED"):
        run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="RUNNING")
    if state not in ("PREPARED", "QUEUED", "RUNNING"):
        run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state=state, fields=fields)
    return run


# --------------------------------------------------------------------------
# sync_task_from_run — status mapping
# --------------------------------------------------------------------------


def test_sync_task_from_run_running_sets_launch_status_running(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="RUNNING")
    task = _make_task()

    mutated = task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert mutated is True
    assert task["launch_status"] == "Running"
    assert task["current_run_id"] == run["id"]


def test_sync_task_from_run_completed_sets_launch_status_completed_and_extracts_fields(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="COMPLETED", completed_at="2026-01-01T00:01:00")
    db.append_run_event(
        db_path, run["id"], "result",
        {"result": "Verdict: APPROVED FOR COMMIT\nPR: https://example.invalid/pr/1\nCommit: abcdef1"},
    )
    db.create_report(db_path, run["id"], f"reports/AIOS/{run['id'][:8]}.md")
    task = _make_task()

    mutated = task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert mutated is True
    assert task["launch_status"] == "Completed"
    assert task["latest_verdict"] == "APPROVED_FOR_COMMIT"
    assert task["pull_request_url"] == "https://example.invalid/pr/1"
    assert task["report_path"] == f"reports/AIOS/{run['id'][:8]}.md"
    assert len(task["timeline"]) == 1
    assert task["timeline"][0]["type"] == "completed"

    reloaded_run = db.get_run(db_path, run["id"])
    assert reloaded_run["commit_hash"] == "abcdef1"
    assert reloaded_run["pull_request_url"] == "https://example.invalid/pr/1"


def test_sync_task_from_run_failed_sets_launch_status_failed(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="FAILED", completed_at="2026-01-01T00:01:00")
    task = _make_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task["launch_status"] == "Failed"
    assert task["timeline"][0]["type"] == "launch_requires_attention"


def test_sync_task_from_run_interrupted_and_unknown_map_to_requires_attention(tmp_path):
    for state in ("INTERRUPTED", "UNKNOWN"):
        db_path = tmp_path / f"runtime-{state}.db"
        db.migrate(db_path)
        run = _make_run(db_path, state=state, completed_at="2026-01-01T00:01:00")
        task = _make_task()

        task_sync.sync_task_from_run(task, run, db_path=db_path)

        assert task["launch_status"] == "Requires Attention", state


def test_sync_task_from_run_cancelled_maps_to_failed_launch_status(tmp_path):
    """`models.LAUNCH_STATUSES` has no dedicated Cancelled value — this is
    the documented simplification (see task_sync.py's module docstring)."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="CANCELLED", completed_at="2026-01-01T00:01:00")
    task = _make_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task["launch_status"] == "Failed"


def test_sync_task_from_run_waiting_maps_to_requires_attention(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="RUNNING")
    run = db.update_run_fields(db_path, run["id"], expected_version=run["version"], fields={"cancel_requested": 1})
    task = _make_task()

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task["launch_status"] == "Requires Attention"


# --------------------------------------------------------------------------
# Manual progress override protection
# --------------------------------------------------------------------------


def test_sync_task_from_run_never_touches_progress_or_workflow_stage(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="COMPLETED", completed_at="2026-01-01T00:01:00")
    task = _make_task(progress=77, progress_mode="manual", workflow_stage="Final Review", current_stage="Validation")

    task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert task["progress"] == 77
    assert task["progress_mode"] == "manual"
    assert task["workflow_stage"] == "Final Review"
    assert task["current_stage"] == "Validation"


# --------------------------------------------------------------------------
# Idempotency: re-syncing an already-terminal run must not re-append
# timeline events or re-parse the report text a second time.
# --------------------------------------------------------------------------


def test_sync_task_from_run_is_idempotent_for_the_same_terminal_run(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="COMPLETED", completed_at="2026-01-01T00:01:00")
    db.append_run_event(db_path, run["id"], "result", {"result": "Verdict: APPROVED FOR COMMIT"})
    task = _make_task()

    parse_calls = []
    original_parse = report_parser.parse_report

    def counting_parse(text):
        parse_calls.append(text)
        return original_parse(text)

    monkeypatch.setattr(task_sync.report_parser, "parse_report", counting_parse)

    first = task_sync.sync_task_from_run(task, run, db_path=db_path)
    second = task_sync.sync_task_from_run(dict(task), run, db_path=db_path)

    assert first is True
    # Re-running against a task that already reflects this exact run's
    # terminal outcome must not mutate it again or re-parse the report.
    assert second is False
    assert len(parse_calls) == 1
    assert len(task["timeline"]) == 1


def test_sync_task_from_run_running_updates_are_cheap_and_repeatable(tmp_path):
    """Recomputing `derive_status` every refresh tick for a still-Running
    run is expected and cheap — only the one-time terminal work is guarded."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_run(db_path, state="RUNNING")
    task = _make_task()

    first = task_sync.sync_task_from_run(task, run, db_path=db_path)
    second = task_sync.sync_task_from_run(task, run, db_path=db_path)

    assert first is True
    assert second is False  # nothing changed the second time — same run, same status
    assert task["launch_status"] == "Running"


# --------------------------------------------------------------------------
# reconcile_and_sync — orchestration over ExecutionCenterAPI + task list
# --------------------------------------------------------------------------


def test_reconcile_and_sync_skips_tasks_without_current_run_id(tmp_path):
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    tasks = [_make_task(current_run_id=None)]

    mutated = task_sync.reconcile_and_sync(api, tasks)

    assert mutated == []
    assert tasks[0]["launch_status"] == "Ready"


def test_reconcile_and_sync_reconciles_dead_process_and_marks_task_requires_attention(tmp_path):
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    run = _make_run(api.db_path, state="RUNNING")

    dead = subprocess.Popen(["true"])
    dead.wait()
    run = db.update_run_fields(api.db_path, run["id"], expected_version=run["version"], fields={"pid": dead.pid})

    task = _make_task(current_run_id=run["id"])
    tasks = [task]

    mutated = task_sync.reconcile_and_sync(api, tasks)

    assert mutated == [task]
    assert task["launch_status"] == "Requires Attention"
    reconciled_run = db.get_run(api.db_path, run["id"])
    assert reconciled_run["state"] == "INTERRUPTED"


def test_reconcile_and_sync_ignores_missing_run(tmp_path):
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    task = _make_task(current_run_id="does-not-exist")

    mutated = task_sync.reconcile_and_sync(api, [task])

    assert mutated == []
