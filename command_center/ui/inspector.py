"""Contextual Inspector panel (UX-2c).

A real detail pane for the *currently selected* task or run, replacing the
UX-1 placeholder. Selection lives in session state under two keys so any view
(a Kanban card, an Execution Center run row, the top-bar search) can target
the same pane without coupling:

* ``inspector_task_id`` — a Kanban task id; resolved against ``tasks_by_id``.
* ``inspector_run_id``  — a v2 runtime run id; resolved via
  ``ExecutionCenterAPI.get_run``.

Exactly one is set at a time; selecting one clears the other. When neither is
set the pane shows an empty state pointing at the search box and the
inspect buttons on cards.

The pane is rendered inside the top-bar popover (`top_bar.render_top_bar`) so
mounting it changes no page's layout — it only ever appears on demand. A
"Открыть полностью" button hands off to the existing task-detail dialog
(``open_task_detail_id`` session key in `app.py`) for the full editable card,
keeping the Inspector read-mostly.
"""

from __future__ import annotations

import streamlit as st

from command_center.ui import tokens

# Session-state keys — the contract between selectors and this pane.
INSPECTOR_TASK_KEY = "inspector_task_id"
INSPECTOR_RUN_KEY = "inspector_run_id"

# Reuse app.py's existing task-detail dialog trigger key (kept as a string to
# avoid importing app.py from here — circular). See `_OPEN_TASK_DETAIL_KEY`.
_OPEN_TASK_DETAIL_KEY = "open_task_detail_id"


def select_task(task_id: str | None) -> None:
    """Select a task into the Inspector (clears any selected run)."""
    if task_id is None:
        st.session_state.pop(INSPECTOR_TASK_KEY, None)
    else:
        st.session_state[INSPECTOR_TASK_KEY] = task_id
    st.session_state.pop(INSPECTOR_RUN_KEY, None)


def select_run(run_id: str | None) -> None:
    """Select a run into the Inspector (clears any selected task)."""
    if run_id is None:
        st.session_state.pop(INSPECTOR_RUN_KEY, None)
    else:
        st.session_state[INSPECTOR_RUN_KEY] = run_id
    st.session_state.pop(INSPECTOR_TASK_KEY, None)


def current_label(tasks_by_id: dict[str, dict]) -> str:
    """The popover label — reflects the current selection so the top-bar
    button always says what the pane will show."""
    task_id = st.session_state.get(INSPECTOR_TASK_KEY)
    if task_id:
        task = tasks_by_id.get(task_id)
        if task:
            title = task.get("title") or "Без названия"
            return f"🔍 {title[:24]}"
    run_id = st.session_state.get(INSPECTOR_RUN_KEY)
    if run_id:
        return f"🔍 run {str(run_id)[:8]}"
    return "Инспектор"


def _render_search(tasks_by_id: dict[str, dict]) -> None:
    """Quick task search -> select into the Inspector."""
    query = st.text_input(
        "Поиск задачи",
        key="inspector_search",
        placeholder="Название задачи…",
        label_visibility="collapsed",
    )
    q = (query or "").strip().lower()
    if not q:
        return
    matches = [t for t in tasks_by_id.values() if q in (t.get("title") or "").lower()]
    if not matches:
        st.caption("Ничего не найдено.")
        return
    for task in matches[:8]:
        if st.button(
            f"{task.get('title') or 'Без названия'} · {task.get('project') or '—'}",
            key=f"inspector_search_{task.get('id')}",
            icon=":material/search:",
            width="stretch",
        ):
            select_task(task.get("id"))
            st.rerun()


def _render_task_detail(task: dict, tasks_by_id: dict[str, dict]) -> None:
    title = task.get("title") or "Без названия"
    st.markdown(f"##### {title}")
    st.caption(f"{task.get('project') or '—'} · {task.get('task_type') or '—'} · `{task.get('id')}`")

    with st.container(horizontal=True):
        priority = task.get("priority", "Medium")
        st.badge(priority, color=tokens.PRIORITY_COLORS.get(priority, "blue"))
        launch_status = task.get("launch_status") or "Ready"
        st.badge(launch_status, color=tokens.LAUNCH_STATUS_COLORS.get(launch_status, "gray"))
        st.badge(task.get("status") or "—", color="gray")

    progress = int(task.get("progress") or 0)
    st.progress(progress / 100, text=f"{task.get('current_stage') or '—'} — {progress}%")

    if task.get("goal"):
        st.markdown(f"🎯 **Цель.** {task['goal']}")
    if task.get("latest_verdict"):
        st.caption(f"Последний вердикт: **{task['latest_verdict']}**")
    if task.get("pull_request_url"):
        st.link_button("Pull Request", task["pull_request_url"], icon=":material/merge:")

    deps = task.get("depends_on") or []
    if deps:
        done = sum(1 for d in deps if (tasks_by_id.get(d) or {}).get("status") == "Done")
        st.caption(f"Зависимости: {len(deps)} (выполнено: {done})")
    st.caption(f"Репозиторий: `{task.get('repository_path') or '—'}`")

    foot = st.columns([1, 1])
    with foot[0]:
        if st.button("Открыть полностью", key="inspector_open_full", icon=":material/open_in_full:", width="stretch"):
            st.session_state[_OPEN_TASK_DETAIL_KEY] = task.get("id")
            st.rerun()
    with foot[1]:
        if st.button("Снять выделение", key="inspector_clear_task", icon=":material/close:", width="stretch"):
            select_task(None)
            st.rerun()


def _render_run_detail(api, run: dict) -> None:
    run_id = run.get("id")
    st.markdown(f"##### Прогон `{str(run_id)[:8]}`")
    st.caption(f"{run.get('project') or '—'} · {run.get('task_type') or '—'} · Executor: `{run.get('executor') or '—'}`")

    with st.container(horizontal=True):
        state = run.get("state") or "—"
        tone = "green" if state == "COMPLETED" else "blue" if state == "RUNNING" else "red" if state == "FAILED" else "gray"
        st.badge(state, color=tone)

    for label, value in [
        ("Запущен", run.get("started_at") or "—"),
        ("Завершён", run.get("completed_at") or "—"),
        ("Ветка", run.get("branch") or "—"),
        ("Exit code", str(run.get("exit_code")) if run.get("exit_code") is not None else "—"),
    ]:
        st.caption(f"{label}: `{value}`")
    if run.get("failure_reason"):
        st.warning(f"Причина: {run['failure_reason']}")

    task_id = run.get("task_id")
    foot = st.columns([1, 1, 1])
    with foot[0]:
        if st.button("В Execution Center", key="inspector_to_exec", icon=":material/monitoring:", width="stretch"):
            st.session_state.pending_nav = "execution_center"
            st.session_state.pending_exec_center_run = run_id
            st.rerun()
    with foot[1]:
        if task_id and st.button("К задаче", key="inspector_to_task", icon=":material/task_alt:", width="stretch"):
            select_task(task_id)
            st.rerun()
    with foot[2]:
        if st.button("Снять", key="inspector_clear_run", icon=":material/close:", width="stretch"):
            select_run(None)
            st.rerun()


def render_inspector(*, tasks_by_id: dict[str, dict], api) -> None:
    """Render the Inspector pane. Always shows the search box; below it, the
    detail for the current selection or an empty state."""
    with st.container(border=True, gap=tokens.SPACE_SM):
        st.caption("Инспектор")
        _render_search(tasks_by_id)

        task_id = st.session_state.get(INSPECTOR_TASK_KEY)
        run_id = st.session_state.get(INSPECTOR_RUN_KEY)

        if task_id and task_id in tasks_by_id:
            _render_task_detail(tasks_by_id[task_id], tasks_by_id)
        elif run_id:
            run = api.get_run(run_id)
            if run:
                _render_run_detail(api, run)
            else:
                st.info("Прогон не найден — возможно, он удалён.", icon=":material/info:")
                if st.button("Снять выделение", key="inspector_clear_missing_run", width="stretch"):
                    select_run(None)
                    st.rerun()
        else:
            st.info(
                "Выберите задачу поиском сверху или кнопкой 🔍 на карточке, "
                "чтобы увидеть детали здесь.",
                icon=":material/info:",
            )


def render_placeholder() -> None:
    """Back-compat shim — the UX-1 entry point (no data available)."""
    st.info("Инспектор: выберите задачу или прогон, чтобы увидеть детали.", icon=":material/info:")
