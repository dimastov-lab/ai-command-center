from command_center import execution_queue, models
from command_center.runtime import api as runtime_api


def _task(**overrides):
    task = {
        "id": overrides.get("id", "t"),
        "project": "AIOS",
        "title": "Task",
        "status": "Backlog",
        "priority": "Medium",
        "depends_on": [],
    }
    task.update(models.default_task_execution_fields())
    task.update(models.default_task_workflow_fields())
    task.update(overrides)
    return task


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_load_queue_empty_when_missing(tmp_path):
    assert execution_queue.load_queue(tmp_path) == []


def test_save_and_load_queue_round_trips(tmp_path):
    entries = [{"id": "q1", "task_id": "t1", "state": execution_queue.STATE_WAITING}]
    execution_queue.save_queue(tmp_path, entries)
    assert execution_queue.load_queue(tmp_path) == entries


# --------------------------------------------------------------------------
# Enqueue / dequeue
# --------------------------------------------------------------------------


def test_enqueue_adds_ready_entry_for_unblocked_task():
    task = _task(id="a")
    tasks_by_id = {"a": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    assert len(entries) == 1
    assert entries[0]["task_id"] == "a"
    assert entries[0]["state"] == execution_queue.STATE_READY


def test_enqueue_adds_waiting_entry_for_blocked_task():
    blocker = _task(id="a")
    blocked = _task(id="b", depends_on=["a"])
    tasks_by_id = {"a": blocker, "b": blocked}
    entries = execution_queue.enqueue([], blocked, tasks_by_id)
    assert entries[0]["state"] == execution_queue.STATE_WAITING
    assert "a" in entries[0]["reason"] or blocker["title"] in entries[0]["reason"]


def test_enqueue_is_idempotent_for_already_open_entry():
    task = _task(id="a")
    tasks_by_id = {"a": task}
    once = execution_queue.enqueue([], task, tasks_by_id)
    twice = execution_queue.enqueue(once, task, tasks_by_id)
    assert len(twice) == 1


def test_enqueue_refuses_done_task():
    task = _task(id="a", status="Done")
    entries = execution_queue.enqueue([], task, {"a": task})
    assert entries == []


def test_dequeue_removes_entry_by_id():
    entries = [{"id": "q1", "task_id": "a"}, {"id": "q2", "task_id": "b"}]
    remaining = execution_queue.dequeue(entries, "q1")
    assert [e["id"] for e in remaining] == ["q2"]


# --------------------------------------------------------------------------
# Deterministic readiness evaluation
# --------------------------------------------------------------------------


def test_evaluate_readiness_transitions_waiting_to_ready_once_dependency_done():
    blocker = _task(id="a", status="Backlog")
    blocked = _task(id="b", depends_on=["a"])
    tasks_by_id = {"a": blocker, "b": blocked}
    entries = execution_queue.enqueue([], blocked, tasks_by_id)
    assert entries[0]["state"] == execution_queue.STATE_WAITING

    blocker["status"] = "Done"
    reevaluated = execution_queue.evaluate_readiness(entries, tasks_by_id)
    assert reevaluated[0]["state"] == execution_queue.STATE_READY
    assert reevaluated[0]["reason"] is None


def test_evaluate_readiness_cancels_entry_whose_task_is_done():
    task = _task(id="a")
    tasks_by_id = {"a": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    task["status"] = "Done"
    reevaluated = execution_queue.evaluate_readiness(entries, tasks_by_id)
    assert reevaluated[0]["state"] == execution_queue.STATE_CANCELLED


def test_evaluate_readiness_cancels_entry_whose_task_no_longer_exists():
    task = _task(id="a")
    entries = execution_queue.enqueue([], task, {"a": task})
    reevaluated = execution_queue.evaluate_readiness(entries, {})
    assert reevaluated[0]["state"] == execution_queue.STATE_CANCELLED


def test_evaluate_readiness_never_touches_terminal_entries():
    launched = {"id": "q1", "task_id": "a", "state": execution_queue.STATE_LAUNCHED, "run_id": "r1"}
    reevaluated = execution_queue.evaluate_readiness([launched], {})
    assert reevaluated[0] == launched


def test_reevaluate_and_persist_saves_updated_state(tmp_path):
    blocker = _task(id="a", status="Backlog")
    blocked = _task(id="b", depends_on=["a"])
    tasks_by_id = {"a": blocker, "b": blocked}
    execution_queue.save_queue(tmp_path, execution_queue.enqueue([], blocked, tasks_by_id))

    blocker["status"] = "Done"
    result = execution_queue.reevaluate_and_persist(tmp_path, tasks_by_id)
    assert result[0]["state"] == execution_queue.STATE_READY
    assert execution_queue.load_queue(tmp_path)[0]["state"] == execution_queue.STATE_READY


def test_ready_and_waiting_entries_filter_correctly():
    entries = [
        {"id": "q1", "state": execution_queue.STATE_READY},
        {"id": "q2", "state": execution_queue.STATE_WAITING},
        {"id": "q3", "state": execution_queue.STATE_LAUNCHED},
    ]
    assert [e["id"] for e in execution_queue.ready_entries(entries)] == ["q1"]
    assert [e["id"] for e in execution_queue.waiting_entries(entries)] == ["q2"]


# --------------------------------------------------------------------------
# launch_ready — the only function in this module that launches anything,
# and only for entries already READY, only when explicitly called (never
# from evaluate_readiness/reevaluate_and_persist).
# --------------------------------------------------------------------------


def test_launch_ready_skips_entry_with_no_workspace_configured(tmp_path):
    task = _task(id="a")
    tasks_by_id = {"a": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": {}}, api
    )
    assert results[0].launched is False
    assert "workspace" in results[0].message
    # No `validate_launch` run happened for this skip reason (no workspace to
    # even validate) — nothing to itemize, so both stay empty.
    assert results[0].warnings == []
    assert results[0].validation_report is None
    assert updated[0]["state"] == execution_queue.STATE_READY  # left in place, not silently dropped


def test_launch_ready_skips_entry_needing_warning_acknowledgement(tmp_path, git_repo):
    # A dirty tree produces a validation *warning*, which launch_ready must
    # never auto-acknowledge on the user's behalf.
    (git_repo / "untracked.txt").write_text("x")
    task = _task(id="a", workspace_path=str(git_repo))
    tasks_by_id = {"a": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": {"repository_path": str(git_repo)}}, api
    )
    assert results[0].launched is False
    assert "подтверждения" in results[0].message
    assert updated[0]["state"] == execution_queue.STATE_READY

    # The exact `validate_launch` warning strings are surfaced separately
    # from the generic `message` — so the UI can render each as its own
    # bullet instead of paraphrasing them away.
    assert len(results[0].warnings) == 1
    assert "Рабочее дерево не чистое" in results[0].warnings[0]

    # ...alongside the complete validation report, for an optional "Details"
    # expander — never just the warning strings in isolation.
    report = results[0].validation_report
    assert report["workspace_path"] == str(git_repo)
    assert report["errors"] == []
    assert report["warnings"] == results[0].warnings
    assert report["git_status"]["dirty"] is True
    assert report["git_status"]["untracked_count"] == 1


def test_launch_ready_skipped_result_warnings_never_used_to_gate_launch(tmp_path, git_repo):
    # Regression guard for "do not change launch behavior, only visibility":
    # populating `warnings`/`validation_report` on the result must not by
    # itself alter whether the entry stays queued — it is still left in
    # STATE_READY, still not launched, exactly as before this field existed.
    (git_repo / "untracked.txt").write_text("x")
    task = _task(id="a", workspace_path=str(git_repo))
    tasks_by_id = {"a": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": {}}, api
    )
    assert results[0].run_id is None
    assert updated[0]["state"] == execution_queue.STATE_READY
    assert updated[0]["run_id"] is None


def test_launch_ready_launches_clean_ready_entry(tmp_path, git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task = _task(id="a", workspace_path=str(git_repo))
    tasks_by_id = {"a": task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": {"repository_path": str(git_repo)}}, api
    )

    assert results[0].launched is True
    assert results[0].run_id is not None
    assert updated[0]["state"] == execution_queue.STATE_LAUNCHED
    assert updated[0]["run_id"] == results[0].run_id
    assert task["current_run_id"] == results[0].run_id

    api.request_cancel(results[0].run_id, confirmed=True)


def test_launch_ready_with_entry_ids_launches_only_that_entry(tmp_path, git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task_a = _task(id="a", workspace_path=str(git_repo))
    task_b = _task(id="b", workspace_path=str(git_repo))  # same workspace — would conflict if both launched
    tasks_by_id = {"a": task_a, "b": task_b}
    entries = execution_queue.enqueue([], task_a, tasks_by_id)
    entries = execution_queue.enqueue(entries, task_b, tasks_by_id)
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    entry_a = next(e for e in entries if e["task_id"] == "a")
    updated, results = execution_queue.launch_ready(
        tmp_path,
        entries,
        [task_a, task_b],
        tasks_by_id,
        {"AIOS": {"repository_path": str(git_repo)}},
        api,
        entry_ids=[entry_a["id"]],
    )

    assert len(results) == 1
    assert results[0].task_id == "a"
    launched_entry = next(e for e in updated if e["task_id"] == "a")
    still_ready_entry = next(e for e in updated if e["task_id"] == "b")
    assert launched_entry["state"] == execution_queue.STATE_LAUNCHED
    assert still_ready_entry["state"] == execution_queue.STATE_READY

    api.request_cancel(results[0].run_id, confirmed=True)


def test_launch_ready_one_bad_entry_does_not_abort_the_batch(tmp_path, git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    broken = _task(id="broken", project="MISSING")  # no workspace or project fallback
    healthy = _task(id="healthy", workspace_path=str(git_repo))
    tasks_by_id = {"broken": broken, "healthy": healthy}
    entries = execution_queue.enqueue([], broken, tasks_by_id)
    entries = execution_queue.enqueue(entries, healthy, tasks_by_id)
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path,
        entries,
        [broken, healthy],
        tasks_by_id,
        {"AIOS": {"repository_path": str(git_repo)}},
        api,
    )

    by_task = {r.task_id: r for r in results}
    assert by_task["broken"].launched is False
    assert by_task["healthy"].launched is True

    api.request_cancel(by_task["healthy"].run_id, confirmed=True)


def test_module_never_constructs_a_git_subprocess_call():
    import ast
    import inspect

    source = inspect.getsource(execution_queue)
    tree = ast.parse(source)
    string_literals = {
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "git" not in string_literals


def test_explicit_remediation_retries_recovered_sole_provider(monkeypatch):
    task = {"executor": "claude_code", "failed_executors": ["claude_code"]}
    cfg = {"allowed_agents": ["claude_code"]}
    availability = type("Availability", (), {"available": True})()
    provider = type("Provider", (), {"availability": lambda self: availability})()
    monkeypatch.setattr(
        "command_center.runtime.providers.get_provider", lambda _provider_id: provider
    )

    assert execution_queue.select_available_executor(task, cfg) is None
    assert execution_queue.select_remediation_executor(task, cfg) == "claude_code"


def test_explicit_remediation_stays_blocked_when_sole_provider_is_unavailable(monkeypatch):
    task = {"executor": "claude_code", "failed_executors": ["claude_code"]}
    cfg = {"allowed_agents": ["claude_code"]}
    availability = type("Availability", (), {"available": False})()
    provider = type("Provider", (), {"availability": lambda self: availability})()
    monkeypatch.setattr(
        "command_center.runtime.providers.get_provider", lambda _provider_id: provider
    )

    assert execution_queue.select_remediation_executor(task, cfg) is None


def test_missing_launched_run_becomes_idempotent_traceable_tombstone(tmp_path):
    from command_center.runtime import db

    db.migrate(db.resolve_db_path(tmp_path))
    entries = [{
        "id": "q1", "task_id": "t1", "project": "AICC",
        "state": execution_queue.STATE_LAUNCHED, "run_id": "deleted-run",
    }]

    first = execution_queue.reconcile_missing_run_links(tmp_path, entries)
    second = execution_queue.reconcile_missing_run_links(tmp_path, first)

    assert first == second
    assert first[0]["state"] == execution_queue.STATE_ATTENTION
    assert first[0]["run_id"] == "deleted-run"
    assert first[0]["missing_run_tombstone"]["run_id"] == "deleted-run"
