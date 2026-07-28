"""End-to-end desktop-autopilot scenario (AICC-DESKTOP-020) — the final gate.

One test drives the whole product objective through the real machinery:

    dependency wave -> parallel launch -> completion -> merge -> next wave

Only two things are stand-ins, and both are the *same* ones the existing
supervisor and completion suites already use:

  * the `claude` binary (`tests/fixtures/fake_claude.py`) — a real
    `subprocess.Popen` with a real pid and real signal handling, so launches,
    reconciliation and run finalization are genuine;
  * the GitHub API (`runtime.github.FakeGitHubClient`) — with its `on_merge`
    hook wired to a *real* git merge into a *real* local bare remote, so
    pushing, merging and target-branch verification exercise actual git.

Everything else — the queue, the scheduler, the launcher, the completion state
machine, the Kanban projection, the task store — is production code.

The one thing the test does on the agent's behalf is `git commit` the change
the fake `claude` writes into the workspace. A real implementation run commits
its own work; the fake only touches a file. Without that commit the completion
evaluator would (correctly) refuse to proceed on an uncommitted tree, which is
a property already covered directly in `test_completion_scenarios.py`.
"""

from __future__ import annotations

import pytest

from command_center import (
    execution_queue,
    models,
    pipeline_settings,
    project_config,
    task_pipeline,
    tasks_repository,
)
from command_center.pipeline_settings import PipelineSettings
from command_center.runtime import api as runtime_api
from command_center.runtime import completion as completion_domain
from command_center.runtime import db as runtime_db
from command_center.runtime import scheduler
from command_center.runtime.github import FakeGitHubClient
from tests.completion_helpers import build_repo, git, merge_into_main
from tests.test_task_pipeline import _drain_runs


def _task(task_id, project, workspace, *, branch, depends_on=None):
    task = {
        "id": task_id,
        "project": project,
        "title": f"Task {task_id}",
        "status": "Backlog",
        "priority": "High",
        "depends_on": depends_on or [],
        "task_type": "implementation",
    }
    task.update(models.default_task_execution_fields())
    task.update(models.default_task_workflow_fields())
    task["workspace_path"] = str(workspace)
    task["branch"] = branch
    return task


@pytest.fixture
def api(tmp_path):
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    yield api
    _drain_runs(api)


def _project_repo(tmp_path, project, name):
    """A real bare remote plus the project's primary working clone on `main`,
    registered as `project`'s repository in the (isolated) project config.

    The task's own workspace is deliberately *not* created here. A feature-branch
    launch must run in an isolated linked worktree, never in the primary working
    tree (`workspace_provisioning.is_feature_task` -> the `isolated_worktree_
    required` gate), so the pipeline provisions it itself at launch time. That is
    the production path, and exercising it is the point — an earlier draft of
    this test pointed the task straight at `work` and was correctly refused."""
    remote, work = build_repo(tmp_path / name)
    project_config.save_project_settings(
        project, repository_path=str(work), default_branch="main"
    )
    return remote, work


def _commit_agent_work(work, message="agent work"):
    """Commit whatever the fake agent left in the working tree, as a real
    implementation run would have done itself."""
    git(work, "add", "-A")
    git(work, "commit", "-m", message)


def test_dependency_wave_parallel_launch_completion_merge_and_next_wave(
    tmp_path, api, fake_claude
):
    # ---------------------------------------------------------------- setup
    pipeline_settings.save_settings(
        tmp_path,
        PipelineSettings(
            enabled=True,
            auto_launch=True,
            auto_merge_after_checks=True,
            max_global_concurrency=4,
            max_agent_concurrency=4,
        ),
    )
    remote_a, _work_a = _project_repo(tmp_path, "AIOS", "proj-a")
    remote_b, _work_b = _project_repo(tmp_path, "AICC", "proj-b")
    _remote_c, _work_c = _project_repo(tmp_path, "AICOS", "proj-c")

    # Worktree paths that do not exist yet — the pipeline creates them.
    wt_a = tmp_path / "wt" / "a"
    wt_b = tmp_path / "wt" / "b"
    wt_c = tmp_path / "wt" / "c"
    task_a = _task("a", "AIOS", wt_a, branch="task/a")
    task_b = _task("b", "AICC", wt_b, branch="task/b")
    task_c = _task("c", "AICOS", wt_c, branch="task/c", depends_on=["a", "b"])
    tasks = [task_a, task_b, task_c]
    tasks_repository.save_tasks(tmp_path, tasks)
    tasks_by_id = {t["id"]: t for t in tasks}
    for task in tasks:
        execution_queue.enqueue_and_persist(tmp_path, task, tasks_by_id)

    configs = project_config.load_project_configs()
    # `merge_into_main` clones into `tmp_path/server-<branch>`, so one shared
    # parent directory still gives each branch its own throwaway server clone.
    merges = {
        "task/a": lambda pr: merge_into_main(remote_a, tmp_path, pr.head_ref),
        "task/b": lambda pr: merge_into_main(remote_b, tmp_path, pr.head_ref),
    }
    github = FakeGitHubClient(on_merge=lambda pr: merges[pr.head_ref](pr))

    # ------------------------------------------------- 1. the dependency wave
    first = task_pipeline.tick(tmp_path, api, configs, github=github, advance_wait_seconds=60)
    assert first.ran is True

    launched = {d.task_id for d in first.launched()}
    assert launched == {"a", "b"}, [d.as_dict() for d in first.decisions]
    assert len({d.run_id for d in first.launched()}) == 2
    assert len({d.workspace for d in first.launched()}) == 2
    # The isolated worktrees were provisioned by the launch itself.
    assert wt_a.is_dir() and wt_b.is_dir()

    # `c` is dependency-blocked, so it is not even a candidate: the queue keeps
    # it WAITING and it never reaches the planner.
    assert "c" not in {d.task_id for d in first.decisions}
    waiting = execution_queue.waiting_entries(execution_queue.load_queue(tmp_path))
    assert [e["task_id"] for e in waiting] == ["c"]

    # --------------------------------------- 2. the agents finish their work
    for run in api.list_runs():
        assert api.supervisor.wait_for_run(run["id"], timeout=60)["state"] == "COMPLETED"
    _commit_agent_work(wt_a, "implement task a")
    _commit_agent_work(wt_b, "implement task b")

    # ------------------------------- 3. completion -> PR -> merge -> verified
    second = task_pipeline.tick(tmp_path, api, configs, github=github, advance_wait_seconds=60)
    assert second.ran is True
    assert not second.errors, second.errors

    # The host-level auto-merge opt-in reached both rows...
    assert {u["to"] for u in second.merge_policy_updates} <= {
        completion_domain.MERGE_AUTO_AFTER_CHECKS
    }
    # ...and the pipeline drove each of them all the way to verified-in-target.
    for task_id in ("a", "b"):
        row = runtime_db.get_completion_by_task(api.db_path, task_id)
        assert row is not None, f"no completion row for {task_id}"
        assert row["completion_state"] == completion_domain.CompletionState.COMPLETED, (
            f"{task_id}: {row['completion_state']} / {row['recommended_action']}"
        )
        assert row["pull_request_number"] is not None
        assert row["merge_commit"]
    assert {pr.head_ref for pr in github.created} == {"task/a", "task/b"}

    # `Done` means the merge is verified in the target branch — not "a PR exists".
    assert set(second.completed_task_ids) == {"a", "b"}
    persisted = {t["id"]: t for t in tasks_repository.load_tasks(tmp_path)}
    assert persisted["a"]["status"] == "Done"
    assert persisted["b"]["status"] == "Done"
    assert persisted["a"]["pull_request_status"] == "merged"
    assert persisted["a"]["progress"] == 100

    # ----------------------------------------------------- 4. the next wave
    # The whole point of the ordering: the Done projection happens *before* the
    # plan step, so `c`'s dependencies are re-evaluated and it is planned and
    # launched on this very same tick — not one tick later.
    by_task = {d.task_id: d for d in second.decisions}
    assert "c" in by_task
    assert by_task["c"].action == scheduler.ACTION_ASSIGN
    assert by_task["c"].agent_id == "claude_code"
    assert by_task["c"].attempt == 1
    assert {d.task_id for d in second.launched()} == {"c"}
    assert wt_c.is_dir()

    # Nothing is left ready afterwards, so the re-planned wave is empty — and a
    # further tick must not start `c` a second time.
    assert second.next_wave == ()
    third = task_pipeline.tick(tmp_path, api, configs, github=github, advance_wait_seconds=60)
    assert third.launched() == []
    assert len(api.list_runs()) == 3


def test_auto_merge_opt_in_off_stops_at_an_open_pull_request(tmp_path, api, fake_claude):
    """The same pipeline with auto-merge withheld: it still validates, pushes
    and opens the PR, but stops there — nothing is merged and the task is not
    Done. This is what makes the opt-in meaningful rather than decorative."""
    pipeline_settings.save_settings(
        tmp_path,
        PipelineSettings(enabled=True, auto_launch=True, auto_merge_after_checks=False),
    )
    _project_repo(tmp_path, "AIOS", "proj-a")
    worktree = tmp_path / "wt" / "a"
    task = _task("a", "AIOS", worktree, branch="task/a")
    tasks_repository.save_tasks(tmp_path, [task])
    execution_queue.enqueue_and_persist(tmp_path, task, {"a": task})
    configs = project_config.load_project_configs()
    github = FakeGitHubClient()

    first = task_pipeline.tick(tmp_path, api, configs, github=github, advance_wait_seconds=60)
    assert first.launched(), [d.as_dict() for d in first.decisions]
    for run in api.list_runs():
        api.supervisor.wait_for_run(run["id"], timeout=60)
    _commit_agent_work(worktree)

    result = task_pipeline.tick(tmp_path, api, configs, github=github, advance_wait_seconds=60)
    row = runtime_db.get_completion_by_task(api.db_path, "a")
    assert row["pull_request_number"] is not None
    assert row["completion_state"] != completion_domain.CompletionState.COMPLETED
    assert github.merged == []
    assert result.completed_task_ids == ()
    assert tasks_repository.load_tasks(tmp_path)[0]["status"] != "Done"
