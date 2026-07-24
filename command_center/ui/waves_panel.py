"""Renders the Waves surface: every declared parallel group, its progress, and
a one-click "enqueue this whole wave" action.

A *renderer* over `command_center.waves` (the pure grouping) and
`command_center.execution_queue` (the enqueue side effect). It computes no
membership of its own and starts no process: pressing a wave's button adds that
wave's not-yet-done, not-yet-queued tasks to the execution queue through the
same locked `enqueue_and_persist` every other enqueue uses, and the autopilot
(or the operator) launches them from there under the usual capacity and
isolation gates. Enqueuing is idempotent — a task already queued is skipped —
so pressing a wave button twice never double-queues anything.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from command_center import execution_queue, waves

_FLASH_KEY = "waves_enqueue_flash"


def render_waves_page(
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    root: Path,
    *,
    project: str | None = None,
) -> None:
    """The Waves page. `project`, if given, restricts the view to one project's
    waves; `None` shows every wave across the portfolio."""
    st.markdown("### Волны")
    st.caption(
        "Группы задач, объявленные для параллельного запуска (поле "
        "`parallel_group`). Кнопка ставит всю волну в очередь — дальше её ведёт "
        "автопилот с учётом лимитов и изоляции worktree. Повторное нажатие "
        "безопасно: уже поставленные задачи пропускаются."
    )

    # A message produced right before `st.rerun()` is discarded by that rerun
    # (the same durability issue the queue and recommendation panels solve);
    # stash it and render it on the next run.
    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        if flash["added"]:
            st.success(f"Волна «{flash['label']}»: поставлено в очередь {flash['added']} задач.")
        else:
            st.info(f"Волна «{flash['label']}»: все задачи уже завершены или в очереди.")

    queue_entries = execution_queue.load_queue(root)
    wave_list = waves.build_waves(tasks, queue_entries, project=project)
    if not wave_list:
        st.info("Нет задач с признаком волны (`parallel_group`).")
        return

    open_waves = [w for w in wave_list if not w.complete]
    done_waves = [w for w in wave_list if w.complete]

    st.caption(f"Открытых волн: {len(open_waves)} · завершённых: {len(done_waves)}")

    for wave in open_waves:
        _render_wave(wave, root, tasks_by_id)

    if done_waves:
        with st.expander(f"Завершённые волны ({len(done_waves)})"):
            for wave in done_waves:
                st.caption(f"✅ {wave.label} — {wave.done}/{wave.total}")


def _render_wave(wave: waves.Wave, root: Path, tasks_by_id: dict[str, dict]) -> None:
    with st.container(border=True):
        header = st.columns([3, 2, 2])
        header[0].markdown(f"**{wave.label}**")
        header[1].caption(
            f"готово {wave.done}/{wave.total}"
            + (f" · в очереди {wave.queued}" if wave.queued else "")
        )
        pending = wave.enqueueable_ids
        with header[2]:
            if st.button(
                f"В очередь ({len(pending)})",
                key=f"wave_enqueue_{wave.label}",
                disabled=not pending,
                type="primary",
                width="stretch",
            ):
                _enqueue_wave(wave, root, tasks_by_id)

        for task in wave.tasks:
            mark = "✅" if task.done else ("🟡" if task.queued else "⚪️")
            state = "готово" if task.done else ("в очереди" if task.queued else "не запущена")
            st.caption(
                f"{mark} {task.title} · приоритет {task.priority or '—'} · {state}"
            )


def _enqueue_wave(wave: waves.Wave, root: Path, tasks_by_id: dict[str, dict]) -> None:
    """Add the wave's pending tasks to the execution queue, then flash and
    rerun. `enqueue_and_persist` is idempotent per task, so the membership was
    computed from the same queue snapshot but a task queued by a concurrent
    action in between is still not double-added."""
    added = 0
    for task_id in wave.enqueueable_ids:
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        before = execution_queue.load_queue(root)
        already = any(
            e.get("task_id") == task_id and e.get("state") in execution_queue.OPEN_STATES
            for e in before
        )
        if already:
            continue
        execution_queue.enqueue_and_persist(root, task, tasks_by_id)
        added += 1
    st.session_state[_FLASH_KEY] = {"label": wave.label, "added": added}
    st.rerun()


# Thin adapter for the app's page registry, matching the signature the other
# pages use (tasks, tasks_by_id, root, plus optional project).
def render(
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    root: Path,
    *,
    project: str | None = None,
    save_tasks_fn: Callable[[list[dict]], None] | None = None,
) -> None:
    render_waves_page(tasks, tasks_by_id, root, project=project)
