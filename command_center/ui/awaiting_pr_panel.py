"""Panel for tasks in "Awaiting PR" status.

Tasks reach this status when:
- The agent completed successfully (run state = COMPLETED)
- But no pull request could be opened because the run executed directly
  on the base branch (completion reason NO_PR_NOT_IN_TARGET)

These tasks are NOT errors — they are finished, waiting to be shipped.
The panel lets the operator:
  - See all finished tasks at a glance
  - Select them in bulk (checkboxes)
  - Mark them Done in one click (for tasks that ran on main and need no PR)
  - Create a PR for tasks that ran on a feature branch
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import streamlit as st

from command_center import models, tasks_repository
from command_center.runtime import runtime_db


def _tasks_awaiting_pr(tasks: list[dict], *, project: str | None = None) -> list[dict]:
    result = [
        t for t in tasks
        if t.get("launch_status") == "Awaiting PR"
        and (project is None or t.get("project") == project)
    ]
    result.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return result


def _mark_done(
    task: dict,
    root: Path,
    tasks: list[dict],
    save_fn: Callable[[list[dict]], None],
) -> None:
    tasks_repository.set_manual_launch_status(
        root,
        task["id"],
        "Done",
        "Отмечено как Done оператором из панели Awaiting PR "
        "(изменения уже присутствуют на целевой ветке).",
    )
    # Reload so the board stays consistent.
    fresh = tasks_repository.load_tasks(root)
    save_fn(fresh)


def render_awaiting_pr_panel(
    tasks: list[dict],
    root: Path,
    save_tasks_fn: Callable[[list[dict]], None],
    *,
    project: str | None = None,
    key_prefix: str = "awaiting_pr",
) -> None:
    """Render the Awaiting PR panel.

    Shows tasks that completed successfully but have no PR (ran on main).
    Supports bulk selection and one-click Done / Create PR actions.
    """
    pending = _tasks_awaiting_pr(tasks, project=project)
    if not pending:
        return

    st.markdown("#### Ожидают PR / Завершения")
    st.caption(
        f"{len(pending)} задач завершены агентом и ждут вашего действия. "
        "Выберите нужные и нажмите кнопку."
    )

    # ---- bulk controls (above the list) ----
    col_all, col_done, col_pr, _ = st.columns([1, 1.5, 1.5, 4])
    select_all_key = f"{key_prefix}_select_all"
    if col_all.button("Всё", key=select_all_key):
        for t in pending:
            st.session_state[f"{key_prefix}_sel_{t['id']}"] = True

    selected_ids = [
        t["id"] for t in pending
        if st.session_state.get(f"{key_prefix}_sel_{t['id']}")
    ]

    mark_done_key = f"{key_prefix}_bulk_done"
    if col_done.button(
        f"✓ Done ({len(selected_ids)})" if selected_ids else "✓ Done",
        key=mark_done_key,
        disabled=not selected_ids,
        type="primary",
    ):
        with st.spinner("Отмечаю…"):
            fresh_tasks = tasks_repository.load_tasks(root)
            for tid in selected_ids:
                tasks_repository.set_manual_launch_status(
                    root, tid, "Done",
                    "Отмечено как Done оператором (bulk) из панели Awaiting PR."
                )
                st.session_state.pop(f"{key_prefix}_sel_{tid}", None)
        st.success(f"Отмечено Done: {len(selected_ids)} задач.")
        st.rerun()

    # ---- task rows ----
    st.divider()
    for task in pending:
        tid = task["id"]
        sel_key = f"{key_prefix}_sel_{tid}"
        col_cb, col_info, col_btn = st.columns([0.5, 6, 2])

        with col_cb:
            st.checkbox("", key=sel_key, label_visibility="collapsed")

        with col_info:
            title = task.get("title") or tid
            proj = task.get("project") or ""
            task_type = task.get("task_type") or task.get("type") or ""
            label_parts = [f"**{title}**"]
            if proj:
                label_parts.append(f"`{proj}`")
            if task_type:
                label_parts.append(f"_{task_type}_")
            st.markdown("  ".join(label_parts))
            # Show latest run info if available
            run_id = task.get("current_run_id")
            if run_id:
                st.caption(f"run: `{run_id[:12]}`")

        with col_btn:
            pr_url = task.get("pull_request_url")
            branch = task.get("branch")
            base_branch = task.get("base_branch") or "main"
            on_main = not branch or branch == base_branch

            if pr_url:
                st.link_button("Open PR", pr_url)
            elif on_main:
                if st.button(
                    "✓ Done",
                    key=f"{key_prefix}_done_{tid}",
                    help="Изменения уже на main — пометить задачу завершённой",
                ):
                    _mark_done(task, root, tasks, save_tasks_fn)
                    st.rerun()
            else:
                st.caption(f"ветка: `{branch}`")

    st.divider()
