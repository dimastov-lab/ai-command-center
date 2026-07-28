"""Renders the Execution Queue and the founder approval surface.

Nothing in this module launches anything on a plain rerun. Manual queue actions
and orchestrator packages both reach ``execution_queue.launch_ready`` only as
the direct result of their own button click in this render, this run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from command_center import execution_queue, project_config, recommend, task_pipeline
from command_center.runtime import scheduler
from command_center.ui.launch_feedback import render_skipped_launch


ORCHESTRATOR_APPROVAL_FLASH_KEY = "orchestrator_approval_flash"


def _pick_next(ready_entries: list[dict], tasks_by_id: dict[str, dict]) -> dict | None:
    """Highest-priority ready entry, oldest first among ties — the single
    entry "Launch next ready task" acts on."""
    if not ready_entries:
        return None

    def sort_key(entry: dict) -> tuple[int, str]:
        task = tasks_by_id.get(entry.get("task_id"), {})
        priority = task.get("priority", "Medium")
        return (-recommend.PRIORITY_WEIGHT.get(priority, 1), entry.get("added_at") or "")

    return sorted(ready_entries, key=sort_key)[0]


def render_orchestrator_approval(
    decisions: list[task_pipeline.EntryDecision],
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    root: Path,
    execution_center_api,
    project_configs: dict[str, dict],
    save_tasks_fn: Callable[[list[dict]], None],
    *,
    key_prefix: str = "orchestrator_approval",
) -> None:
    """Show and, only on a founder click, dispatch one proposed launch package.

    The proposal is immutable for this render: the exact entry ids, executors,
    branches and worktrees shown here are the exact subset handed to
    ``launch_ready``. That service rechecks queue readiness, capacity, workspace
    ownership and launch safety under its normal lock, so approval cannot turn
    a stale proposal into an unsafe launch.
    """
    assignments = [
        decision
        for decision in decisions
        if decision.action == scheduler.ACTION_ASSIGN
        and decision.entry_id
        and decision.task_id
        and not decision.launched
    ]
    if not assignments:
        return

    st.warning(
        "Требуется решение founder: AI Orchestrator подготовил пакет, "
        "но ни одна задача ещё не запущена."
    )
    st.markdown("#### Подтверждение пакета запусков")
    st.caption(
        f"В пакете {len(assignments)} задач. Проверьте полный список задач, "
        "веток и worktree перед подтверждением."
    )

    for index, decision in enumerate(assignments, start=1):
        task = tasks_by_id.get(decision.task_id, {})
        branch = task.get("branch") or "—"
        workspace = decision.workspace or task.get("workspace_path") or "—"
        st.markdown(f"**{index}. {decision.title or task.get('title') or decision.task_id}**")
        st.caption(
            f"Task: {decision.task_id} · Project: {decision.project or task.get('project') or '—'} "
            f"· Branch: {branch} · Worktree: {workspace} "
            f"· Executor: {decision.executor_id or decision.agent_id or '—'}"
        )

    st.error(
        "Запуск произойдёт только после нажатия founder-кнопки ниже. "
        "Подтверждение относится ко всему показанному пакету."
    )
    approved = st.button(
        f"Founder: подтвердить и запустить пакет ({len(assignments)})",
        key=f"{key_prefix}_confirm",
        type="primary",
        width="stretch",
    )
    if not approved:
        return

    entries = execution_queue.load_queue(root)
    entry_ids = [decision.entry_id for decision in assignments if decision.entry_id]
    executor_by_entry = {
        decision.entry_id: decision.executor_id
        for decision in assignments
        if decision.entry_id and decision.executor_id
    }
    _, results = execution_queue.launch_ready(
        root,
        entries,
        tasks,
        tasks_by_id,
        project_configs,
        execution_center_api,
        entry_ids=entry_ids,
        executor_by_entry=executor_by_entry,
    )
    save_tasks_fn(tasks)
    launched = [result for result in results if result.launched]
    skipped = [result for result in results if not result.launched]
    st.session_state[ORCHESTRATOR_APPROVAL_FLASH_KEY] = {
        "launched_count": len(launched),
        "skipped_count": len(skipped),
    }
    st.rerun()


def render_execution_queue_panel(
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    root: Path,
    execution_center_api,
    project_configs: dict[str, dict],
    save_tasks_fn: Callable[[list[dict]], None],
    *,
    project: str | None = None,
    key_prefix: str = "queue",
    entries: list[dict] | None = None,
    show_heading: bool = True,
) -> None:
    if entries is None:
        entries = execution_queue.reevaluate_and_persist(root, tasks_by_id)
    if project:
        # A queue entry copies its task's raw `project` (which may be a display
        # name), while `project` here is the canonical id emitted by the Kanban
        # selector — match on canonical ids (shared helper) so the queue on the
        # Kanban page shows the same project's tasks the lane above it does.
        entries = [e for e in entries if project_config.project_matches(e.get("project"), project)]

    waiting = execution_queue.waiting_entries(entries)
    ready = execution_queue.ready_entries(entries)

    if show_heading:
        st.markdown("#### Очередь запуска")

    # `launch_ready` runs synchronously right before the `st.rerun()` below,
    # in the *same* script pass as the button click — so a plain `st.success`/
    # `st.warning` call here would be created and then immediately discarded
    # by that rerun before ever reaching the browser (this was the actual
    # root cause of "launch button does nothing": every entry whose
    # `launch.validate_launch` reported a warning — e.g. a dirty working tree,
    # which is the AIOS repository's normal day-to-day state — was silently
    # skipped, and the message explaining *why* never survived long enough
    # to be seen). Stashing it in `session_state` and rendering it on the
    # *next* run (right here, before anything else) is what makes it durable
    # across that rerun.
    flash_key = f"{key_prefix}_launch_flash"
    flash = st.session_state.pop(flash_key, None)
    if flash:
        if flash["launched_count"]:
            st.success(f"Запущено: {flash['launched_count']}.")
        for skipped in flash["skipped"]:
            render_skipped_launch(
                f"Не запущено — {skipped['task_id']}",
                message=skipped["message"],
                warnings=skipped["warnings"],
                validation_report=skipped["validation_report"],
                details_label=skipped["task_id"],
            )

    if not waiting and not ready:
        st.caption("Очередь пуста. Добавьте задачу из рекомендаций или карточки Kanban.")
        return

    button_cols = st.columns(2)
    with button_cols[0]:
        launch_all_clicked = st.button(
            "🚀 Запустить готовые",
            key=f"{key_prefix}_launch_ready",
            type="primary",
            disabled=not ready,
            width="stretch",
        )
    with button_cols[1]:
        launch_next_clicked = st.button(
            "Запустить следующую готовую задачу",
            key=f"{key_prefix}_launch_next",
            disabled=not ready,
            width="stretch",
        )

    if launch_all_clicked or launch_next_clicked:
        entry_ids = None
        if launch_next_clicked:
            next_entry = _pick_next(ready, tasks_by_id)
            entry_ids = [next_entry["id"]] if next_entry else []
        _, results = execution_queue.launch_ready(
            root, entries, tasks, tasks_by_id, project_configs, execution_center_api, entry_ids=entry_ids
        )
        save_tasks_fn(tasks)
        launched = [r for r in results if r.launched]
        skipped = [r for r in results if not r.launched]
        st.session_state[flash_key] = {
            "launched_count": len(launched),
            "skipped": [
                {
                    "task_id": r.task_id,
                    "message": r.message,
                    "warnings": list(r.warnings),
                    "validation_report": r.validation_report,
                }
                for r in skipped
            ],
        }
        st.rerun()

    if ready:
        st.caption(f"Готово к запуску · {len(ready)}")
        for entry in ready:
            task = tasks_by_id.get(entry.get("task_id"), {})
            row = st.columns([4, 1])
            row[0].caption(f"🟢 {task.get('title', entry.get('task_id'))} — {task.get('project', '—')}")
            if row[1].button("Убрать", key=f"{key_prefix}_remove_{entry['id']}"):
                execution_queue.dequeue_and_persist(root, entry["id"])
                st.rerun()

    if waiting:
        st.caption(f"Ожидает зависимостей · {len(waiting)}")
        for entry in waiting:
            task = tasks_by_id.get(entry.get("task_id"), {})
            row = st.columns([4, 1])
            row[0].caption(f"🟡 {task.get('title', entry.get('task_id'))} — {entry.get('reason') or '—'}")
            if row[1].button("Убрать", key=f"{key_prefix}_remove_{entry['id']}"):
                execution_queue.dequeue_and_persist(root, entry["id"])
                st.rerun()
