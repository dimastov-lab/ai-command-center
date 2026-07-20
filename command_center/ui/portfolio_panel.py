"""Portfolio Execution panel: browse `tasks/ready` cards from the Portfolio
repository, dry-run a launch plan, and launch a task (or a batch of tasks)
into its own branch + worktree via `command_center.portfolio_launch`.

Every mutation (repository-mapping save, dry-run, launch) is the direct
result of an explicit button click in this render, this run — matching every
other launch surface in this app (see `command_center.ui.queue_panel`'s
module docstring for the same discipline).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from command_center import portfolio_config, portfolio_launch
from command_center.portfolio_models import PortfolioTask, load_portfolio_tasks

PORTFOLIO_ROOT_ENV = "AICC_PORTFOLIO_ROOT"
DEFAULT_PORTFOLIO_ROOT = Path.home() / "Projects" / "Portfolio"


def portfolio_root() -> Path:
    override = os.environ.get(PORTFOLIO_ROOT_ENV, "").strip()
    return Path(override) if override else DEFAULT_PORTFOLIO_ROOT


def _task_label(task: PortfolioTask) -> str:
    return f"{task.task_id} — {task.title or '(без названия)'} [{task.project}]"


def _render_repository_mapping(project_ids: list[str]) -> None:
    st.markdown("##### Сопоставление проектов с локальными репозиториями")
    mapping = portfolio_config.load_repository_paths()
    for project_id in project_ids:
        current = mapping.get(project_id) or ""
        cols = st.columns([1, 3, 1])
        cols[0].caption(project_id)
        new_value = cols[1].text_input(
            f"Путь к репозиторию — {project_id}",
            value=current,
            key=f"portfolio_repo_path_{project_id}",
            label_visibility="collapsed",
            placeholder="/Users/.../путь/к/репозиторию" if not current else None,
        )
        if cols[2].button("Сохранить", key=f"portfolio_repo_save_{project_id}"):
            if new_value.strip():
                ok, message = portfolio_config.validate_repository_path(new_value.strip())
                if not ok:
                    st.error(message)
                else:
                    portfolio_config.save_repository_path(project_id, new_value.strip())
                    st.success(f"Сохранено: {project_id} → {new_value.strip()}")
                    st.rerun()
            else:
                portfolio_config.save_repository_path(project_id, None)
                st.info(f"Сопоставление для {project_id} очищено.")
                st.rerun()


_SOURCE_LABELS: dict[str, str] = {
    portfolio_launch.SOURCE_CARD_OVERRIDE: "из карточки (override)",
    portfolio_launch.SOURCE_GENERATED_DEFAULT: "сгенерировано по умолчанию",
}

_WORKTREE_MODE_LABELS: dict[str, str] = {
    portfolio_launch.WORKTREE_MODE_EXISTING: "существующий (переиспользуется)",
    portfolio_launch.WORKTREE_MODE_NEW: "новый (будет создан)",
}

# Headline-status badge color per `PortfolioCardPresentation.status_key` —
# the one place this module maps the framework-agnostic `severity`/
# `status_key` from `portfolio_launch` onto Streamlit's `st.badge` palette.
_STATUS_BADGE_COLORS: dict[str, str] = {
    portfolio_launch.STATUS_READY: "green",
    portfolio_launch.STATUS_RUNNING: "blue",
    portfolio_launch.STATUS_COMPLETED: "green",
    portfolio_launch.STATUS_FAILED: "red",
    portfolio_launch.STATUS_CANCELLED: "gray",
    portfolio_launch.STATUS_ALREADY_LAUNCHED: "orange",
    portfolio_launch.STATUS_BLOCKED: "red",
}

_MESSAGE_RENDERERS = {"info": st.info, "warning": st.warning, "error": st.error}


def _render_plan_details(task: PortfolioTask, plan: portfolio_launch.LaunchPlan) -> None:
    st.caption(f"Task workflow status: `{task.status or plan.lane}`")
    st.write(f"- Repository: `{plan.repository_root or '—'}`")
    st.write(f"- Base branch: `{plan.base_branch or '—'}` · SHA: `{plan.base_sha or '—'}`")
    st.write(f"- Запрошенная ветка (карточка): `{plan.requested_branch or '—'}`")
    st.write(f"- Итоговая ветка: `{plan.branch or '—'}` · источник: {_SOURCE_LABELS.get(plan.branch_source, plan.branch_source)}")
    st.write(f"- Запрошенный worktree (карточка): `{plan.requested_worktree or '—'}`")
    st.write(
        f"- Итоговый worktree: `{plan.worktree or '—'}` · источник: {_SOURCE_LABELS.get(plan.worktree_source, plan.worktree_source)}"
    )
    st.write(f"- Режим worktree: {_WORKTREE_MODE_LABELS.get(plan.worktree_mode, plan.worktree_mode)}")
    st.write(f"- Тип задачи для агента: `{plan.task_type}`")
    # The "already launched" blocker is rendered as its own info/warning
    # message above the expander (via `PortfolioCardPresentation.message`) —
    # it is not a precondition error, so it is deliberately excluded from
    # this `st.error` loop to avoid showing it twice, once correctly framed
    # and once as a red error.
    for blocker in plan.blockers:
        if blocker == portfolio_launch.ALREADY_LAUNCHED_BLOCKER:
            continue
        st.error(blocker)
    for warning in plan.warnings:
        st.warning(warning)
    if plan.launchable:
        st.success("Готово к запуску.")


def render_portfolio_execution_panel(
    *,
    root: Path,
    execution_center_api,
    key_prefix: str = "portfolio",
) -> None:
    st.markdown("#### Portfolio Execution")
    st.caption("Загрузка и запуск задач из Portfolio (tasks/ready) через AI Command Center.")

    p_root = portfolio_root()
    result = load_portfolio_tasks(p_root)

    if result.missing:
        st.error(f"Portfolio не найден по пути: `{p_root}`. Настройте `{PORTFOLIO_ROOT_ENV}` или создайте checkout.")
        return

    if result.card_issues:
        with st.expander(f"Проблемы с карточками ({len(result.card_issues)})", expanded=False):
            for issue in result.card_issues:
                st.warning(f"`{issue.source_path}`: {issue.message}")

    ready_tasks = result.ready_tasks()
    tasks_by_id = result.tasks_by_id()

    project_ids = sorted({task.project for task in result.tasks if task.project})
    with st.expander("Настройка репозиториев", expanded=not project_ids == []):
        _render_repository_mapping(project_ids)

    st.divider()

    if not ready_tasks:
        st.info("Нет готовых задач (tasks/ready) для запуска.")
        return

    repository_paths = portfolio_config.load_repository_paths()
    registry = portfolio_launch.load_registry(root)

    st.markdown(f"##### Готовые задачи ({len(ready_tasks)})")

    selected_key = f"{key_prefix}_selected"
    st.session_state.setdefault(selected_key, set())

    for task in ready_tasks:
        plan = portfolio_launch.build_launch_plan(
            task, tasks_by_id=tasks_by_id, repository_paths=repository_paths, registry=registry
        )
        registry_entry = registry.get(task.task_id) if task.task_id else None
        existing_run = (
            execution_center_api.get_run(registry_entry["run_id"])
            if registry_entry and registry_entry.get("run_id")
            else None
        )
        presentation = portfolio_launch.build_card_presentation(
            plan, registry_entry=registry_entry, existing_run=existing_run
        )

        with st.container(border=True):
            header_cols = st.columns([0.5, 3, 1.5])
            checked = header_cols[0].checkbox(
                "Выбрать",
                key=f"{key_prefix}_check_{task.task_id}",
                label_visibility="collapsed",
                disabled=not presentation.launch_allowed,
            )
            if checked:
                st.session_state[selected_key].add(task.task_id)
            else:
                st.session_state[selected_key].discard(task.task_id)

            header_cols[1].markdown(f"**{_task_label(task)}**")
            header_cols[2].badge(
                presentation.status_label,
                color=_STATUS_BADGE_COLORS.get(presentation.status_key, "gray"),
            )

            if presentation.message:
                _MESSAGE_RENDERERS[presentation.message_severity](presentation.message)

            if presentation.existing_run_id:
                if st.button(
                    "Open run",
                    key=f"{key_prefix}_open_run_{task.task_id}",
                    icon=":material/open_in_new:",
                ):
                    st.session_state.pending_nav = "execution_center"
                    st.session_state.pending_exec_center_run = presentation.existing_run_id
                    st.rerun()

            with st.expander("Детали и dry-run"):
                _render_plan_details(task, plan)

            action_cols = st.columns(2)
            with action_cols[0]:
                if st.button("Dry run", key=f"{key_prefix}_dryrun_{task.task_id}", width="stretch"):
                    st.session_state[f"{key_prefix}_dryrun_shown_{task.task_id}"] = True
            with action_cols[1]:
                launch_disabled = not presentation.launch_allowed
                if st.button(
                    "Launch",
                    key=f"{key_prefix}_launch_open_{task.task_id}",
                    type="primary",
                    disabled=launch_disabled,
                    width="stretch",
                ):
                    st.session_state[f"{key_prefix}_confirm_{task.task_id}"] = True

            if st.session_state.get(f"{key_prefix}_dryrun_shown_{task.task_id}"):
                st.info("Dry-run: показанный план не создаёт ветки, worktree, prompt или запись очереди.")

            if st.session_state.get(f"{key_prefix}_confirm_{task.task_id}"):
                confirmed = st.checkbox(
                    "Я подтверждаю запуск: будет создана ветка, worktree и запущен агент.",
                    key=f"{key_prefix}_confirmed_{task.task_id}",
                )
                confirm_cols = st.columns(2)
                with confirm_cols[0]:
                    if st.button(
                        "Подтвердить и запустить",
                        key=f"{key_prefix}_confirm_launch_{task.task_id}",
                        type="primary",
                        disabled=not confirmed,
                    ):
                        # Defense in depth: `disabled=` above is the primary
                        # gate, but a forced click on a disabled button (a
                        # real browser could never send one, but a test — or
                        # a future UI bug — could) must still not launch
                        # anything without the checkbox actually being
                        # checked in this run's own session state.
                        if not confirmed:
                            st.error("Подтвердите запуск флажком выше.")
                        else:
                            launch_result = portfolio_launch.launch_portfolio_task(
                                root,
                                task,
                                tasks_by_id=tasks_by_id,
                                repository_paths=repository_paths,
                                execution_center_api=execution_center_api,
                                confirmed=True,
                            )
                            st.session_state[f"{key_prefix}_confirm_{task.task_id}"] = False
                            if launch_result.launched:
                                st.success(f"Запущено: run `{launch_result.run_id}`.")
                            else:
                                st.error(f"Не запущено: {launch_result.message}")
                            st.rerun()
                with confirm_cols[1]:
                    if st.button("Отмена", key=f"{key_prefix}_confirm_cancel_{task.task_id}"):
                        st.session_state[f"{key_prefix}_confirm_{task.task_id}"] = False
                        st.rerun()

    st.divider()
    st.markdown("##### Пакетный запуск")
    selected_ids = st.session_state.get(selected_key, set())
    st.caption(f"Выбрано: {len(selected_ids)}")

    if selected_ids:
        batch_confirm_key = f"{key_prefix}_batch_confirm"
        if st.button("Launch выбранные", key=f"{key_prefix}_batch_open"):
            st.session_state[batch_confirm_key] = True

        if st.session_state.get(batch_confirm_key):
            confirmed = st.checkbox(
                "Я подтверждаю пакетный запуск всех выбранных задач.",
                key=f"{key_prefix}_batch_confirmed",
            )
            if st.button(
                "Подтвердить пакетный запуск",
                key=f"{key_prefix}_batch_launch",
                type="primary",
                disabled=not confirmed,
            ):
                # Same forced-click defense in depth as the single-task
                # confirm button above.
                if not confirmed:
                    st.error("Подтвердите пакетный запуск флажком выше.")
                else:
                    selected_tasks = [task for task in ready_tasks if task.task_id in selected_ids]
                    results = portfolio_launch.launch_batch(
                        root,
                        selected_tasks,
                        tasks_by_id=tasks_by_id,
                        repository_paths=repository_paths,
                        execution_center_api=execution_center_api,
                        confirmed=True,
                    )
                    st.session_state[batch_confirm_key] = False
                    st.session_state[selected_key] = set()
                    for launch_result in results:
                        if launch_result.launched:
                            st.success(f"{launch_result.task_id}: запущено, run `{launch_result.run_id}`.")
                        else:
                            st.error(f"{launch_result.task_id}: не запущено — {launch_result.message}")
                    st.rerun()

    if registry:
        st.divider()
        st.markdown("##### Журнал запусков Portfolio")
        for task_id, entry in sorted(registry.items(), key=lambda kv: kv[1].get("launched_at") or "", reverse=True):
            st.caption(
                f"`{task_id}` · {entry.get('project')} · run `{entry.get('run_id')}` · "
                f"branch `{entry.get('branch')}` · {entry.get('launched_at')}"
            )
