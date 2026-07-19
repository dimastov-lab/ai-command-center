"""Renders the upgraded "Recommended Next Tasks" surface: rationale,
dependencies, impact, readiness, and a direct Launch-now or Add-to-Queue
action per recommendation — over `command_center.recommendation_service`
(itself a thin decorator around the unmodified `command_center.recommend`
scoring engine)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from command_center import execution_queue, recommendation_service

_QUEUED_STATE_LABELS: dict[str, str] = {
    execution_queue.STATE_WAITING: "В очереди · ожидает",
    execution_queue.STATE_READY: "В очереди · готово",
}


def render_recommendations_panel(
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    root: Path,
    execution_center_api,
    project_configs: dict[str, dict],
    save_tasks_fn: Callable[[list[dict]], None],
    *,
    project: str | None = None,
    limit: int = 3,
    key_prefix: str = "reco",
) -> None:
    queue_entries = execution_queue.reevaluate_and_persist(root, tasks_by_id)
    views = recommendation_service.build_recommendation_views(
        tasks, tasks_by_id, project=project, queue_entries=queue_entries, limit=limit
    )

    st.markdown("#### Рекомендованные задачи")
    if not views:
        st.info("Нет рекомендованных незаблокированных задач.")
        return

    columns = st.columns(len(views))
    for column, view in zip(columns, views, strict=True):
        with column, st.container(border=True):
            st.markdown(f"**{view['title']}**")
            st.caption(f"{view['project']} · приоритет {view['priority']}")
            st.caption("Почему: " + "; ".join(view["reasons"]))

            if view["dependencies"]:
                dep_summary = ", ".join(
                    f"{dep['title'] or dep['id']} ({'готово' if dep['done'] else dep['status'] or '?'})"
                    for dep in view["dependencies"]
                )
                st.caption(f"Зависимости: {dep_summary}")
            else:
                st.caption("Зависимости: нет")

            st.caption(
                f"Влияние: разблокирует {view['impact']} задач(и)"
                if view["impact"]
                else "Влияние: не блокирует другие задачи"
            )
            st.caption("Готовность: " + ("готово к запуску" if view["ready"] else "заблокировано"))

            queued_label = _QUEUED_STATE_LABELS.get(view["queued_state"] or "")
            if queued_label:
                st.caption(queued_label)

            action_cols = st.columns(2)
            with action_cols[0]:
                queue_disabled = view["queued_state"] is not None
                if st.button(
                    "В очередь",
                    key=f"{key_prefix}_{view['task_id']}_enqueue",
                    disabled=queue_disabled,
                    width="stretch",
                ):
                    task = tasks_by_id.get(view["task_id"])
                    updated = execution_queue.enqueue(queue_entries, task, tasks_by_id)
                    execution_queue.save_queue(root, updated)
                    st.rerun()

            with action_cols[1]:
                if st.button(
                    "Запустить",
                    key=f"{key_prefix}_{view['task_id']}_launch",
                    type="primary",
                    width="stretch",
                ):
                    task = tasks_by_id.get(view["task_id"])
                    working_entries = execution_queue.enqueue(queue_entries, task, tasks_by_id)
                    new_entry = next(
                        e
                        for e in working_entries
                        if e.get("task_id") == view["task_id"] and e.get("state") in execution_queue.OPEN_STATES
                    )
                    _, results = execution_queue.launch_ready(
                        root,
                        working_entries,
                        tasks,
                        tasks_by_id,
                        project_configs,
                        execution_center_api,
                        entry_ids=[new_entry["id"]],
                    )
                    save_tasks_fn(tasks)
                    result = results[0] if results else None
                    if result and result.launched:
                        st.success(f"Запуск начат: `{result.run_id}`.")
                    elif result:
                        st.warning(f"Не удалось запустить сразу: {result.message}")
                    st.rerun()
