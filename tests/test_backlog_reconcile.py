"""Backlog reconciliation — detecting open tasks that are already done, so the
same work is never launched twice (`command_center.backlog_reconcile`)."""

from __future__ import annotations

from command_center import backlog_reconcile as br


def _task(task_id, title, *, status="Backlog", goal=None, launch_status=None, created_at="2026-01-01"):
    return {
        "id": task_id,
        "title": title,
        "goal": goal or title,
        "status": status,
        "launch_status": launch_status,
        "created_at": created_at,
    }


def test_self_completed_task_is_flagged_to_mark_done():
    tasks = [_task("t1", "Ship the thing", launch_status="Completed")]
    findings = br.find_reconcilable(tasks)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == br.KIND_SELF_COMPLETED
    assert f.suggested_action == "mark_done"
    assert not f.is_delete


def test_open_task_matching_a_done_task_is_flagged_to_delete():
    tasks = [
        _task("done1", "Audit Task Model Ordering and Progress", status="Done"),
        _task("open1", "Audit task-model, ordering & progress!"),  # same work, punctuation differs
    ]
    findings = br.find_reconcilable(tasks)
    assert len(findings) == 1
    f = findings[0]
    assert f.task_id == "open1"
    assert f.kind == br.KIND_DUPLICATE_OF_DONE
    assert f.suggested_action == "delete"
    assert f.match_task_id == "done1"


def test_two_open_duplicates_flag_the_newer_one():
    tasks = [
        _task("old", "Reliable execution queue", created_at="2026-01-01"),
        _task("new", "Reliable execution queue", created_at="2026-02-01"),
    ]
    findings = br.find_reconcilable(tasks)
    assert len(findings) == 1
    assert findings[0].task_id == "new"  # newer is redundant; older kept
    assert findings[0].kind == br.KIND_DUPLICATE_OPEN
    assert findings[0].match_task_id == "old"


def test_distinct_tasks_are_not_flagged():
    tasks = [
        _task("a", "Add PDF export to reports"),
        _task("b", "Fix the login race condition"),
        _task("c", "Write the deployment runbook", status="Done"),
    ]
    assert br.find_reconcilable(tasks) == []


def test_each_task_yields_at_most_one_finding():
    # self_completed wins over duplicate_of_done for the same task.
    tasks = [
        _task("done", "Build the widget", status="Done"),
        _task("open", "Build the widget", launch_status="Completed"),
    ]
    findings = br.find_reconcilable(tasks)
    open_findings = [f for f in findings if f.task_id == "open"]
    assert len(open_findings) == 1
    assert open_findings[0].kind == br.KIND_SELF_COMPLETED


def test_threshold_is_respected():
    tasks = [
        _task("done", "Implement OAuth login flow with Google", status="Done"),
        _task("open", "Implement OAuth login flow with Google"),  # identical -> flagged
    ]
    assert len(br.find_reconcilable(tasks, threshold=0.9)) == 1
    # An absurd threshold flags nothing.
    assert br.find_reconcilable(tasks, threshold=1.01) == []


def test_done_tasks_are_never_themselves_flagged():
    tasks = [_task("d1", "x", status="Done"), _task("d2", "x", status="Done")]
    assert br.find_reconcilable(tasks) == []
