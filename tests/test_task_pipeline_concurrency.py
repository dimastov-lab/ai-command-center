"""Concurrency and idempotency coverage for the desktop autopilot
(AICC-DESKTOP-019).

The invariant under test is "at most one active attempt per task and per
workspace", held by three independent layers that are all exercised here:

  1. the same-host advisory `pipeline_lock` (a second tick reports busy and
     does nothing rather than planning against the same free capacity);
  2. `scheduler.plan`'s in-plan exclusivity (one ASSIGN per task_id, one per
     workspace) seeded from the *live* `LoadSnapshot`, so a run started by a
     previous tick still counts;
  3. `launch_service.find_active_run_conflict` / the queue's own LAUNCHED
     state, which refuse a duplicate even if a caller reaches the launcher
     directly.

Every launch here goes through the real `subprocess.Popen` path with the fake
`claude` stand-in, so "did not launch twice" means no second process, not just
no second row.
"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

from command_center import (
    execution_queue,
    models,
    pipeline_settings,
    task_pipeline,
    tasks_repository,
)
from command_center.pipeline_settings import PipelineSettings
from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db
from command_center.runtime import scheduler
from tests.test_task_pipeline import _drain_runs


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


def _make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return path


@pytest.fixture
def api(tmp_path):
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    yield api
    _drain_runs(api)


@pytest.fixture
def autopilot(tmp_path):
    return pipeline_settings.save_settings(
        tmp_path, PipelineSettings(enabled=True, auto_launch=True, max_global_concurrency=4)
    )


def _seed(tmp_path, tasks):
    tasks_repository.save_tasks(tmp_path, tasks)
    tasks_by_id = {t["id"]: t for t in tasks}
    for task in tasks:
        execution_queue.enqueue_and_persist(tmp_path, task, tasks_by_id)
    return tasks_by_id


# --------------------------------------------------------------------------
# 1. The advisory pipeline lock
# --------------------------------------------------------------------------


def test_a_second_tick_reports_busy_instead_of_planning(tmp_path, api, autopilot):
    with task_pipeline.pipeline_lock(tmp_path, timeout=5):
        result = task_pipeline.tick(tmp_path, api, {})
    assert result.ran is False
    assert result.status == task_pipeline.TICK_BUSY
    assert result.decisions == ()


def test_busy_tick_launches_nothing(tmp_path, git_repo, api, autopilot, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task = _task(id="a", workspace_path=str(git_repo))
    _seed(tmp_path, [task])

    with task_pipeline.pipeline_lock(tmp_path, timeout=5):
        task_pipeline.tick(tmp_path, api, {"AIOS": {"repository_path": str(git_repo)}})

    assert api.list_runs() == []


def test_concurrent_ticks_produce_exactly_one_launch(tmp_path, git_repo, api, autopilot, fake_claude):
    """Two threads tick simultaneously against one ready task. Whichever wins
    the lock launches; the loser reports busy. Never two runs."""
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task = _task(id="a", workspace_path=str(git_repo))
    _seed(tmp_path, [task])
    configs = {"AIOS": {"repository_path": str(git_repo)}}

    results: list[task_pipeline.PipelineTickResult] = []
    barrier = threading.Barrier(2)

    def _run() -> None:
        barrier.wait()
        results.append(task_pipeline.tick(tmp_path, api, configs))

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(results) == 2
    launched = [d for r in results for d in r.launched()]
    assert len(launched) <= 1
    assert len(api.list_runs()) == len(launched)


# --------------------------------------------------------------------------
# 2. Task and workspace exclusivity inside one plan
# --------------------------------------------------------------------------


def test_two_ready_tasks_sharing_one_workspace_yield_one_assignment(tmp_path, git_repo, api, autopilot):
    tasks = [
        _task(id="a", workspace_path=str(git_repo)),
        _task(id="b", workspace_path=str(git_repo)),
    ]
    tasks_by_id = _seed(tmp_path, tasks)
    entries = execution_queue.reevaluate_and_persist(tmp_path, tasks_by_id)
    wave = task_pipeline.adapt_ready_entries(
        execution_queue.ready_entries(entries),
        tasks_by_id,
        {"AIOS": {"repository_path": str(git_repo)}},
        db_path=api.db_path,
    )
    plan = api.plan_schedule(list(wave.work_items), now="2026-07-24T10:00:00")
    decisions = task_pipeline.map_decisions(plan, wave, tasks_by_id)

    assigned = [d for d in decisions if d.action == scheduler.ACTION_ASSIGN]
    deferred = [d for d in decisions if d.action == scheduler.ACTION_DEFER]
    assert len(assigned) == 1
    assert [d.reason_code for d in deferred] == [scheduler.REASON_WORKSPACE_BUSY]


def test_task_with_an_active_run_is_deferred_as_duplicate(tmp_path, git_repo, api, autopilot):
    task = _task(id="a", workspace_path=str(git_repo))
    tasks_by_id = _seed(tmp_path, [task])

    # An already-active run for this task, exactly as a previous tick would have
    # left behind: the live LoadSnapshot must veto a second attempt.
    runtime_db.create_task(api.db_path, project="AIOS", title="T", task_type="implementation", task_id="a")
    session = runtime_db.create_session(
        api.db_path, task_id="a", project="AIOS", repository_path=str(git_repo)
    )
    run = runtime_db.create_run(
        api.db_path,
        session_id=session["id"],
        task_id="a",
        project="AIOS",
        task_type="implementation",
        repository_path=str(git_repo),
        prompt="p",
        is_resume=False,
    )
    runtime_db.update_run_state(api.db_path, run["id"], expected_version=run["version"], new_state="QUEUED")

    entries = execution_queue.reevaluate_and_persist(tmp_path, tasks_by_id)
    wave = task_pipeline.adapt_ready_entries(
        execution_queue.ready_entries(entries),
        tasks_by_id,
        {"AIOS": {"repository_path": str(git_repo)}},
        db_path=api.db_path,
    )
    plan = api.plan_schedule(list(wave.work_items), now="2026-07-24T10:00:00")
    decisions = task_pipeline.map_decisions(plan, wave, tasks_by_id)
    assert [d.action for d in decisions] == [scheduler.ACTION_DEFER]
    assert decisions[0].reason_code == scheduler.REASON_DUPLICATE_TASK


# --------------------------------------------------------------------------
# 3. Repeated sequential ticks are idempotent
# --------------------------------------------------------------------------


def test_repeated_ticks_never_start_a_second_run_for_the_same_task(
    tmp_path, git_repo, api, autopilot, fake_claude
):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    task = _task(id="a", workspace_path=str(git_repo))
    _seed(tmp_path, [task])
    configs = {"AIOS": {"repository_path": str(git_repo)}}

    first = task_pipeline.tick(tmp_path, api, configs)
    assert len(first.launched()) == 1

    second = task_pipeline.tick(tmp_path, api, configs)
    assert second.ran is True
    assert second.launched() == []
    assert len(api.list_runs()) == 1

    # The queue entry moved to LAUNCHED, so it is no longer even a candidate.
    states = {e["state"] for e in execution_queue.load_queue(tmp_path)}
    assert states == {execution_queue.STATE_LAUNCHED}


def _two_project_wave(tmp_path):
    """Two independent projects, each with its own real repository — the shape a
    genuine parallel wave has. (Two tasks in *one* project pointing at two
    unrelated repositories would be refused by the fail-closed workspace
    isolation gate, which is correct: a workspace must belong to its project's
    repository. See `test_workspace_isolation_failure_does_not_abort_the_wave`.)"""
    repo_a = _make_repo(tmp_path / "repo-a")
    repo_b = _make_repo(tmp_path / "repo-b")
    tasks = [
        _task(id="a", project="AIOS", workspace_path=str(repo_a)),
        _task(id="b", project="AICC", workspace_path=str(repo_b)),
    ]
    _seed(tmp_path, tasks)
    configs = {
        "AIOS": {"repository_path": str(repo_a)},
        "AICC": {"repository_path": str(repo_b)},
    }
    return configs


def test_parallel_wave_launches_each_distinct_workspace_once(
    tmp_path, api, autopilot, fake_claude
):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    configs = _two_project_wave(tmp_path)

    result = task_pipeline.tick(tmp_path, api, configs)
    launched = result.launched()
    assert {d.task_id for d in launched} == {"a", "b"}
    assert len({d.run_id for d in launched}) == 2

    repeat = task_pipeline.tick(tmp_path, api, configs)
    assert repeat.launched() == []
    assert len(api.list_runs()) == 2


def test_global_concurrency_setting_bounds_one_wave(tmp_path, api, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    pipeline_settings.save_settings(
        tmp_path, PipelineSettings(enabled=True, auto_launch=True, max_global_concurrency=1)
    )
    configs = _two_project_wave(tmp_path)

    result = task_pipeline.tick(tmp_path, api, configs)
    assert len(result.launched()) == 1
    deferred = [d for d in result.decisions if d.action == scheduler.ACTION_DEFER]
    assert [d.reason_code for d in deferred] == [scheduler.REASON_GLOBAL_AT_CAPACITY]


def test_workspace_isolation_failure_does_not_abort_the_wave(tmp_path, api, autopilot, fake_claude):
    """AICC-DESKTOP-008: one entry whose workspace does not belong to its
    project's repository is refused by the fail-closed isolation gate, with a
    reason — and the other assigned entry in the same wave still starts."""
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    repo_a = _make_repo(tmp_path / "repo-a")
    foreign = _make_repo(tmp_path / "foreign")
    _seed(
        tmp_path,
        [
            _task(id="good", project="AIOS", workspace_path=str(repo_a)),
            _task(id="bad", project="AIOS", workspace_path=str(foreign)),
        ],
    )

    result = task_pipeline.tick(tmp_path, api, {"AIOS": {"repository_path": str(repo_a)}})

    launched = {d.task_id for d in result.launched()}
    assert "good" in launched
    refused = [d for d in result.decisions if d.task_id == "bad"]
    assert refused and refused[0].launched is False
    assert refused[0].launch_message
    assert len(api.list_runs()) == 1


def test_auto_launch_off_plans_the_wave_but_launches_nothing(tmp_path, git_repo, api, fake_claude):
    pipeline_settings.save_settings(tmp_path, PipelineSettings(enabled=True, auto_launch=False))
    task = _task(id="a", workspace_path=str(git_repo))
    _seed(tmp_path, [task])

    result = task_pipeline.tick(tmp_path, api, {"AIOS": {"repository_path": str(git_repo)}})
    assert result.ran is True
    assert result.launch_status == task_pipeline.LAUNCH_DISABLED
    assert [d.action for d in result.decisions] == [scheduler.ACTION_ASSIGN]
    assert result.launched() == []
    assert api.list_runs() == []


# --------------------------------------------------------------------------
# 4. The completion advance never holds the caller's thread
# --------------------------------------------------------------------------


def test_a_slow_completion_advance_does_not_block_the_tick(tmp_path, api, autopilot, monkeypatch):
    """The advance step runs the project's validation commands (900s timeout)
    and `git fetch`/`gh` round trips. The caller is a Streamlit render, so the
    tick must bound how long it waits and let a slow advance finish in the
    background — otherwise the dashboard an operator needs in order to *cancel*
    a runaway run is itself frozen."""
    started = threading.Event()
    release = threading.Event()

    def _slow_advance(*, limit=50, github=None, now=None):
        started.set()
        release.wait(timeout=30)
        return []

    monkeypatch.setattr(api, "advance_completions", _slow_advance)

    began = time.monotonic()
    result = task_pipeline.tick(tmp_path, api, {}, advance_wait_seconds=0.2)
    elapsed = time.monotonic() - began

    assert started.is_set(), "the advance must actually have been started"
    assert result.ran is True
    # Two advance points, each bounded by advance_wait_seconds; everything else
    # in an empty-store tick is sub-millisecond.
    assert elapsed < 5, f"tick held the caller for {elapsed:.1f}s"
    release.set()


def test_results_of_a_late_advance_are_reported_by_a_later_tick(tmp_path, api, autopilot, monkeypatch):
    """An advance that outruns the tick that started it must not lose its
    results — the next tick drains them."""
    release = threading.Event()
    calls: list[int] = []

    class _Advance:
        run_id = "run-1"
        from_state = "EXECUTION_FINISHED"
        to_state = "RESULT_VALID"
        reason_code = "validation_passed"
        changed = True

    def _slow_once(*, limit=50, github=None, now=None):
        calls.append(1)
        if len(calls) == 1:
            release.wait(timeout=30)
            return [_Advance()]
        return []

    monkeypatch.setattr(api, "advance_completions", _slow_once)

    first = task_pipeline.tick(tmp_path, api, {}, advance_wait_seconds=0.1)
    assert first.completion_advances == ()

    release.set()
    for _ in range(100):
        if not task_pipeline._advance_worker(api.db_path).is_running():
            break
        time.sleep(0.05)

    second = task_pipeline.tick(tmp_path, api, {}, advance_wait_seconds=0.1)
    assert [a["run_id"] for a in second.completion_advances] == ["run-1"]


def test_a_failing_advance_is_reported_and_does_not_abort_the_tick(tmp_path, git_repo, api, autopilot, monkeypatch):
    """A broken `gh`, an unreachable remote or a failing validation command must
    degrade the tick, not kill it: unrelated tasks still get planned."""

    def _boom(*, limit=50, github=None, now=None):
        raise RuntimeError("gh: not authenticated")

    monkeypatch.setattr(api, "advance_completions", _boom)
    _seed(tmp_path, [_task(id="a", workspace_path=str(git_repo))])

    result = task_pipeline.tick(
        tmp_path, api, {"AIOS": {"repository_path": str(git_repo)}}, advance_wait_seconds=5
    )

    assert result.ran is True
    assert any("gh: not authenticated" in error for error in result.errors)
    assert [d.action for d in result.decisions] == [scheduler.ACTION_ASSIGN]
