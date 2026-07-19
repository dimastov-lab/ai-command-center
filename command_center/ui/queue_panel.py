"""Renders the Execution Queue: Waiting / Ready lists, a prominent "Launch
Ready" control, and an optional "launch next ready task" action — the only
two places in the app that ever call `execution_queue.launch_ready`. Nothing
in this module (or the `execution_queue` service it calls) launches anything
on a plain rerun; every launch here is the direct result of a button click
in this render, this run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from command_center import execution_queue, recommend


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
) -> None:
    entries = execution_queue.reevaluate_and_persist(root, tasks_by_id)
    if project:
        entries = [e for e in entries if e.get("project") == project]

    waiting = execution_queue.waiting_entries(entries)
    ready = execution_queue.ready_entries(entries)

    st.markdown("#### Очередь запуска")
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
        if launched:
            st.success(f"Запущено: {len(launched)}.")
        if skipped:
            with st.expander(f"Пропущено: {len(skipped)}", icon=":material/info:"):
                for result in skipped:
                    st.caption(f"{result.task_id}: {result.message}")
        st.rerun()

    if ready:
        st.caption(f"Готово к запуску · {len(ready)}")
        for entry in ready:
            task = tasks_by_id.get(entry.get("task_id"), {})
            row = st.columns([4, 1])
            row[0].caption(f"🟢 {task.get('title', entry.get('task_id'))} — {task.get('project', '—')}")
            if row[1].button("Убрать", key=f"{key_prefix}_remove_{entry['id']}"):
                execution_queue.save_queue(root, execution_queue.dequeue(entries, entry["id"]))
                st.rerun()

    if waiting:
        st.caption(f"Ожидает зависимостей · {len(waiting)}")
        for entry in waiting:
            task = tasks_by_id.get(entry.get("task_id"), {})
            row = st.columns([4, 1])
            row[0].caption(f"🟡 {task.get('title', entry.get('task_id'))} — {entry.get('reason') or '—'}")
            if row[1].button("Убрать", key=f"{key_prefix}_remove_{entry['id']}"):
                execution_queue.save_queue(root, execution_queue.dequeue(entries, entry["id"]))
                st.rerun()
