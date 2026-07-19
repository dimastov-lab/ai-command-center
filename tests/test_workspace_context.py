"""Tests for `command_center/workspace_context.py` -- `WorkspaceContext`,
persistence round-trip, every reconciliation branch, and `apply()`'s command
fan-out. Per `docs/workspace/IMPLEMENTATION_ROADMAP.md`'s UW-1 exit criteria:
unit tests for every reconciliation branch (deleted task, deleted run,
mismatched project, missing repository path, missing artifact, unknown panel
key), a persistence round-trip, and command fan-out
(`SelectTask`/`OpenRun`/`SelectProject`).

Plain pytest, no Streamlit anywhere -- `workspace_context.py` has no
Streamlit import.
"""

from __future__ import annotations

import pytest

from command_center import project_config, tasks_repository, workspace_context
from command_center.runtime import db as runtime_db


def _make_kanban_task(project: str = "AIOS", **overrides) -> dict:
    task = tasks_repository.new_task_record(
        project=project,
        title="Test task",
        task_type="implementation",
        status="To Do",
    )
    task.update(overrides)
    return task


def _save_task(root, task: dict) -> None:
    tasks_repository.save_tasks(root, [task])


def _make_v2_run(db_path, *, project: str = "AIOS") -> dict:
    runtime_db.migrate(db_path)
    task = runtime_db.create_task(db_path, project=project, title="t", task_type="implementation")
    session = runtime_db.create_session(db_path, task_id=task["id"], project=project, repository_path="/tmp/x")
    return runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project=project,
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )


# --------------------------------------------------------------------------
# Persistence round-trip
# --------------------------------------------------------------------------


def test_save_then_load_round_trips_persisted_fields(tmp_path):
    ctx = workspace_context.WorkspaceContext(
        active_project="AIOS",
        selected_task_id="task-1",
        selected_run_id="run-1",
        selected_repository_path="/tmp/repo",
        active_panel="kanban",  # session-only -- must NOT survive the round trip
        inspector_mode="task",  # session-only -- must NOT survive the round trip
    )
    workspace_context.save(tmp_path, ctx)
    loaded = workspace_context.load(tmp_path)

    for field in workspace_context.PERSISTED_FIELDS:
        assert getattr(loaded, field) == getattr(ctx, field)
    assert loaded.active_panel == "workspace_home"  # back to the session-only default
    assert loaded.inspector_mode is None


def test_load_on_missing_file_returns_defaults(tmp_path):
    ctx = workspace_context.load(tmp_path)
    assert ctx == workspace_context.WorkspaceContext(updated_at=ctx.updated_at)


def test_load_ignores_unknown_schema_version_fields(tmp_path):
    from command_center import storage

    storage.atomic_write_json(
        workspace_context._state_file_path(tmp_path),
        {"active_project": "AIOS", "schema_version": 999, "some_future_field": "x"},
    )
    loaded = workspace_context.load(tmp_path)
    assert loaded.active_project == "AIOS"


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


def test_reconcile_clears_unknown_project(tmp_path):
    ctx = workspace_context.WorkspaceContext(active_project="NOT_A_PROJECT")
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.active_project is None


def test_reconcile_clears_deleted_task(tmp_path):
    ctx = workspace_context.WorkspaceContext(active_project="AIOS", selected_task_id="ghost-task")
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_task_id is None
    assert reconciled.selected_run_id is None


def test_reconcile_fans_out_project_from_valid_task(tmp_path):
    task = _make_kanban_task(project="BUSINESS")
    _save_task(tmp_path, task)
    ctx = workspace_context.WorkspaceContext(active_project="AIOS", selected_task_id=task["id"])
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.active_project == "BUSINESS"
    assert reconciled.selected_task_id == task["id"]


def test_reconcile_clears_deleted_run(tmp_path):
    ctx = workspace_context.WorkspaceContext(selected_run_id="ghost-run")
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_run_id is None


def test_reconcile_fans_out_task_from_valid_run(tmp_path):
    db_path = runtime_db.resolve_db_path(tmp_path)
    run = _make_v2_run(db_path)
    task = _make_kanban_task(project="AIOS", current_run_id=run["id"])
    _save_task(tmp_path, task)

    ctx = workspace_context.WorkspaceContext(selected_run_id=run["id"])
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_task_id == task["id"]
    assert reconciled.active_project == "AIOS"


def test_reconcile_keeps_run_selected_when_no_kanban_task_links_to_it(tmp_path):
    """A run launched outside the Kanban flow has no task with a matching
    `current_run_id` -- the run stays selected, just without a task fan-out."""
    db_path = runtime_db.resolve_db_path(tmp_path)
    run = _make_v2_run(db_path)

    ctx = workspace_context.WorkspaceContext(selected_run_id=run["id"])
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_run_id == run["id"]
    assert reconciled.selected_task_id is None


def test_reconcile_clears_missing_repository_path_and_branch(tmp_path):
    ctx = workspace_context.WorkspaceContext(
        selected_repository_path=str(tmp_path / "does-not-exist"),
        selected_branch="main",
    )
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_repository_path is None
    assert reconciled.selected_branch is None


def test_reconcile_keeps_valid_repository_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = workspace_context.WorkspaceContext(selected_repository_path=str(repo), selected_branch="main")
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_repository_path == str(repo)
    assert reconciled.selected_branch == "main"


def test_reconcile_clears_missing_artifact(tmp_path):
    ctx = workspace_context.WorkspaceContext(selected_artifact_path=str(tmp_path / "gone.md"))
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.selected_artifact_path is None


def test_reconcile_resets_unknown_panel_to_workspace_home(tmp_path):
    ctx = workspace_context.WorkspaceContext(active_panel="no_such_panel")
    reconciled = workspace_context.reconcile(tmp_path, ctx)
    assert reconciled.active_panel == "workspace_home"


def test_reconcile_never_raises_on_fully_default_context(tmp_path):
    reconciled = workspace_context.reconcile(tmp_path, workspace_context.WorkspaceContext())
    assert reconciled == workspace_context.WorkspaceContext(updated_at=reconciled.updated_at)


# --------------------------------------------------------------------------
# apply() -- command fan-out
# --------------------------------------------------------------------------


def test_select_project_sets_active_project(tmp_path):
    ctx = workspace_context.apply(tmp_path, workspace_context.WorkspaceContext(), workspace_context.SelectProject("AIOS"))
    assert ctx.active_project == "AIOS"


def test_select_project_rejects_unknown_project(tmp_path):
    with pytest.raises(ValueError):
        workspace_context.apply(tmp_path, workspace_context.WorkspaceContext(), workspace_context.SelectProject("NOPE"))


def test_select_project_clears_task_from_a_different_project(tmp_path):
    task = _make_kanban_task(project="AIOS")
    _save_task(tmp_path, task)
    ctx = workspace_context.WorkspaceContext(active_project="AIOS", selected_task_id=task["id"])
    updated = workspace_context.apply(tmp_path, ctx, workspace_context.SelectProject("BUSINESS"))
    assert updated.active_project == "BUSINESS"
    assert updated.selected_task_id is None


def test_select_project_keeps_task_from_the_same_project(tmp_path):
    task = _make_kanban_task(project="AIOS")
    _save_task(tmp_path, task)
    ctx = workspace_context.WorkspaceContext(active_project="AIOS", selected_task_id=task["id"])
    updated = workspace_context.apply(tmp_path, ctx, workspace_context.SelectProject("AIOS"))
    assert updated.selected_task_id == task["id"]


def test_select_task_sets_active_project_and_repository_path(tmp_path):
    project_config.save_repository_path("AIOS", str(tmp_path))
    task = _make_kanban_task(project="AIOS")
    _save_task(tmp_path, task)

    updated = workspace_context.apply(
        tmp_path, workspace_context.WorkspaceContext(), workspace_context.SelectTask(task["id"])
    )
    assert updated.selected_task_id == task["id"]
    assert updated.active_project == "AIOS"
    assert updated.selected_repository_path == str(tmp_path)


def test_select_task_rejects_unknown_task(tmp_path):
    with pytest.raises(ValueError):
        workspace_context.apply(tmp_path, workspace_context.WorkspaceContext(), workspace_context.SelectTask("ghost"))


def test_select_task_clears_unrelated_run(tmp_path):
    task = _make_kanban_task(project="AIOS")
    _save_task(tmp_path, task)
    ctx = workspace_context.WorkspaceContext(selected_run_id="some-other-run")
    updated = workspace_context.apply(tmp_path, ctx, workspace_context.SelectTask(task["id"]))
    assert updated.selected_run_id is None


def test_open_run_fans_out_task_and_project(tmp_path):
    db_path = runtime_db.resolve_db_path(tmp_path)
    run = _make_v2_run(db_path, project="AIOS")
    task = _make_kanban_task(project="AIOS", current_run_id=run["id"])
    _save_task(tmp_path, task)

    updated = workspace_context.apply(tmp_path, workspace_context.WorkspaceContext(), workspace_context.OpenRun(run["id"]))
    assert updated.selected_run_id == run["id"]
    assert updated.selected_task_id == task["id"]
    assert updated.active_project == "AIOS"
    assert updated.active_panel == "runtime"
    assert updated.inspector_mode == "run"


def test_open_run_rejects_unknown_run(tmp_path):
    with pytest.raises(ValueError):
        workspace_context.apply(tmp_path, workspace_context.WorkspaceContext(), workspace_context.OpenRun("ghost-run"))


def test_open_artifact_sets_selection(tmp_path):
    artifact = tmp_path / "note.md"
    artifact.write_text("hello")
    updated = workspace_context.apply(
        tmp_path, workspace_context.WorkspaceContext(), workspace_context.OpenArtifact(str(artifact))
    )
    assert updated.selected_artifact_path == str(artifact)
    assert updated.inspector_mode == "artifact"


def test_open_artifact_rejects_missing_path(tmp_path):
    with pytest.raises(ValueError):
        workspace_context.apply(
            tmp_path, workspace_context.WorkspaceContext(), workspace_context.OpenArtifact(str(tmp_path / "gone.md"))
        )


def test_open_branch_sets_repository_and_branch(git_repo):
    from command_center import git_info

    branch = git_info.get_branches(git_repo)[0]
    updated = workspace_context.apply(
        git_repo,
        workspace_context.WorkspaceContext(),
        workspace_context.OpenBranch(str(git_repo), branch),
    )
    assert updated.selected_repository_path == str(git_repo)
    assert updated.selected_branch == branch
    assert updated.active_panel == "git"


def test_open_branch_rejects_invalid_repository_path(tmp_path):
    with pytest.raises(ValueError):
        workspace_context.apply(
            tmp_path,
            workspace_context.WorkspaceContext(),
            workspace_context.OpenBranch(str(tmp_path / "no-repo"), "main"),
        )


# --------------------------------------------------------------------------
# Breadcrumbs
# --------------------------------------------------------------------------


def test_breadcrumbs_empty_when_no_project_selected():
    assert workspace_context.breadcrumbs(workspace_context.WorkspaceContext()) == []


def test_breadcrumbs_truncate_to_non_none_prefix():
    ctx = workspace_context.WorkspaceContext(active_project="AIOS", selected_task_id="t1")
    crumbs = workspace_context.breadcrumbs(ctx)
    assert crumbs == [
        workspace_context.BreadcrumbItem("project", "AIOS"),
        workspace_context.BreadcrumbItem("task", "t1"),
    ]


def test_breadcrumbs_full_chain():
    ctx = workspace_context.WorkspaceContext(active_project="AIOS", selected_task_id="t1", selected_run_id="r1")
    crumbs = workspace_context.breadcrumbs(ctx)
    assert crumbs == [
        workspace_context.BreadcrumbItem("project", "AIOS"),
        workspace_context.BreadcrumbItem("task", "t1"),
        workspace_context.BreadcrumbItem("run", "r1"),
    ]
