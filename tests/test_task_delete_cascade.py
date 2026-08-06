"""Regression for AR-1: deleting a Kanban task must not orphan its runtime.db
rows. There was no `db.delete_task`, so `tasks_repository.delete_task` (which
only rewrites tasks.json) left the task/session/run/run_event/report/completion
rows behind forever, and they kept surfacing in the unified Runs/Timeline/metrics
views. The FK graph already declares ON DELETE CASCADE; the missing piece is the
delete of the `task` row itself.
"""

from __future__ import annotations

from command_center.runtime import db


def _fresh_db(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    return path


def _seed_task_with_run(path, task_id):
    db.create_task(path, project="AICC", title="t", task_type="implementation", task_id=task_id)
    sess = db.create_session(path, task_id=task_id, project="AICC", repository_path="/r")
    run = db.create_run(
        path,
        session_id=sess["id"],
        task_id=task_id,
        project="AICC",
        task_type="implementation",
        repository_path="/r",
        prompt="p",
        is_resume=False,
    )
    db.append_run_event(path, run["id"], "lifecycle", {"x": 1})
    return sess, run


def test_delete_task_cascades_session_run_event(tmp_path):
    path = _fresh_db(tmp_path)
    sess, run = _seed_task_with_run(path, "T-1")
    assert db.get_task(path, "T-1") is not None

    deleted = db.delete_task(path, "T-1")

    assert deleted is True
    assert db.get_task(path, "T-1") is None
    assert db.get_session(path, sess["id"]) is None
    assert db.get_run(path, run["id"]) is None


def test_delete_task_returns_false_for_unknown(tmp_path):
    path = _fresh_db(tmp_path)
    assert db.delete_task(path, "does-not-exist") is False


def test_delete_task_leaves_other_tasks_intact(tmp_path):
    path = _fresh_db(tmp_path)
    _seed_task_with_run(path, "T-1")
    _, other_run = _seed_task_with_run(path, "T-2")

    db.delete_task(path, "T-1")

    assert db.get_task(path, "T-1") is None
    assert db.get_task(path, "T-2") is not None
    assert db.get_run(path, other_run["id"]) is not None
