"""Regression for AR-4: list_unified_runs must be able to bound how many runs it
loads/normalizes, so the hot Runs/Timeline/metrics pages don't fetch the entire
run table (and parse every report) on each render as the lifetime run count
grows. `db.list_runs` already supports `limit`; this threads it through the
unified reader and truncates the merged (v2 + v1.2) result to the same bound.
"""

from __future__ import annotations

from command_center.runtime import db
from command_center.runtime import runs_read


def _seed_v2_runs(path, n):
    for i in range(n):
        tid = f"T{i}"
        db.create_task(path, project="AICC", title=f"t{i}", task_type="implementation", task_id=tid)
        sess = db.create_session(path, task_id=tid, project="AICC", repository_path="/r")
        db.create_run(
            path,
            session_id=sess["id"],
            task_id=tid,
            project="AICC",
            task_type="implementation",
            repository_path="/r",
            prompt="p",
            is_resume=False,
        )


def test_unified_runs_limit_bounds_result(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    _seed_v2_runs(path, 5)

    limited = runs_read.list_unified_runs(path, root=tmp_path, include_v1=False, limit=2)

    assert len(limited) == 2


def test_unified_runs_no_limit_returns_all(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    _seed_v2_runs(path, 5)

    everything = runs_read.list_unified_runs(path, root=tmp_path, include_v1=False)

    assert len(everything) == 5


def test_unified_runs_limit_zero_returns_empty(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    _seed_v2_runs(path, 3)

    assert runs_read.list_unified_runs(path, root=tmp_path, include_v1=False, limit=0) == []
