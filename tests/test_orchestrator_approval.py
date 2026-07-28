"""Founder approval contract for orchestrator-proposed launch packages."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from command_center import execution_queue, task_pipeline
from command_center.pipeline_settings import PipelineSettings
from command_center.runtime import scheduler


def _approval_script() -> None:
    from pathlib import Path

    import streamlit as st

    from command_center.ui import queue_panel

    decisions = st.session_state["decisions"]
    queue_panel.render_orchestrator_approval(
        decisions,
        st.session_state["tasks"],
        {task["id"]: task for task in st.session_state["tasks"]},
        Path(st.session_state["root"]),
        st.session_state["api"],
        st.session_state["project_configs"],
        st.session_state["save_tasks"],
    )


def _decision(entry_id: str, task_id: str, workspace: str) -> task_pipeline.EntryDecision:
    return task_pipeline.EntryDecision(
        entry_id=entry_id,
        task_id=task_id,
        action=scheduler.ACTION_ASSIGN,
        reason_code=scheduler.REASON_ASSIGNED,
        explanation="planned",
        title=f"Task {task_id}",
        project="AICC",
        priority="High",
        workspace=workspace,
        agent_id="codex",
        executor_id="codex",
        attempt=1,
    )


def test_app_autopilot_tick_is_planning_only_even_when_auto_launch_is_enabled(
    monkeypatch,
):
    import app

    persisted = PipelineSettings(
        enabled=True,
        auto_launch=True,
        auto_rework=True,
        auto_remediate_workspace=True,
    )
    observed: list[PipelineSettings] = []

    monkeypatch.setattr(
        task_pipeline.pipeline_settings, "load_settings", lambda _root: persisted
    )
    monkeypatch.setattr(app.project_config, "load_project_configs", lambda: {})

    def tick_spy(_root, _api, _configs, *, settings):
        observed.append(settings)
        return task_pipeline.PipelineTickResult(
            started_at="2026-07-28T10:00:00",
            finished_at="2026-07-28T10:00:01",
            settings=settings,
            ran=True,
            status=task_pipeline.TICK_RAN,
        )

    monkeypatch.setattr(task_pipeline, "tick", tick_spy)

    result = app._run_autopilot_tick(object())

    assert len(observed) == 1
    assert observed[0].enabled is True
    assert observed[0].auto_launch is False
    assert observed[0].auto_rework is False
    assert observed[0].auto_remediate_workspace is False
    assert result.settings == persisted


def test_orchestrator_package_waits_for_founder_and_shows_full_manifest(
    isolated_data_dir, monkeypatch
):
    tasks = [
        {
            "id": "a",
            "title": "Task a",
            "project": "AICC",
            "branch": "feature/a",
            "workspace_path": "/tmp/worktrees/a",
            "status": "Backlog",
            "depends_on": [],
        },
        {
            "id": "b",
            "title": "Task b",
            "project": "AICC",
            "branch": "feature/b",
            "workspace_path": "/tmp/worktrees/b",
            "status": "Backlog",
            "depends_on": [],
        },
    ]
    entries = []
    tasks_by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        entries = execution_queue.enqueue(entries, task, tasks_by_id)
    execution_queue.save_queue(isolated_data_dir, entries)
    decisions = [
        _decision(entries[0]["id"], "a", "/tmp/worktrees/a"),
        _decision(entries[1]["id"], "b", "/tmp/worktrees/b"),
    ]

    calls: list[dict] = []

    def launch_ready_spy(*args, **kwargs):
        calls.append(kwargs)
        return args[1], []

    monkeypatch.setattr(execution_queue, "launch_ready", launch_ready_spy)
    at = AppTest.from_function(_approval_script, default_timeout=30)
    at.session_state["decisions"] = decisions
    at.session_state["tasks"] = tasks
    at.session_state["root"] = str(isolated_data_dir)
    at.session_state["api"] = object()
    at.session_state["project_configs"] = {}
    at.session_state["save_tasks"] = lambda _tasks: None

    at.run()

    assert not at.exception
    assert calls == [], "a plain render must never dispatch an orchestrator package"
    manifest = "\n".join(caption.value for caption in at.caption)
    assert "feature/a" in manifest and "/tmp/worktrees/a" in manifest
    assert "feature/b" in manifest and "/tmp/worktrees/b" in manifest

    confirm = at.button(key="orchestrator_approval_confirm")
    assert "Founder" in confirm.label
    confirm.click().run()

    assert len(calls) == 1
    assert calls[0]["entry_ids"] == [entries[0]["id"], entries[1]["id"]]
    assert calls[0]["executor_by_entry"] == {
        entries[0]["id"]: "codex",
        entries[1]["id"]: "codex",
    }
