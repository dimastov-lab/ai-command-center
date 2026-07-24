"""Renders the Desktop Autopilot surface: the explicit persistent opt-in
controls, the next parallel-safe wave, and the durable outcome of the last
tick.

This module is a *renderer*. It never plans, never launches, never advances a
completion and never writes a task — it displays a `task_pipeline.
PipelineTickResult` that the app's existing refresh path already produced (see
`app.py`'s `_render_live_execution_center_body`), and it writes exactly one kind
of state: the operator's own toggles, through `pipeline_settings.update_settings`
in direct response to a widget change.

Two details are deliberate:

- **The toggles are persisted, not session state.** "Is this machine allowed to
  launch work on my behalf?" must survive a rerun, a page switch and an app
  restart, so each control writes to `data/pipeline_settings.json` on change and
  reads its value back from there — never from `st.session_state`, which would
  silently reset the answer to that question.
- **The tick outcome is stashed, not rendered inline.** Like the queue and
  recommendation panels before it, a message produced during a script pass that
  ends in `st.rerun()` is discarded before the browser sees it. The tick result
  is therefore written to `session_state` by the refresh path and rendered here
  on the *following* run, which is what makes launches, skips and merge results
  actually visible (AICC-DESKTOP-017).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from command_center import pipeline_settings, task_pipeline
from command_center.runtime import scheduler

# Where `app.py`'s refresh path stashes the most recent `PipelineTickResult`,
# and the message from a tick that could not run at all.
TICK_RESULT_KEY = "autopilot_last_tick"
TICK_ERROR_KEY = "autopilot_last_error"

_ACTION_LABELS: dict[str, str] = {
    scheduler.ACTION_ASSIGN: "🟢 Назначено",
    scheduler.ACTION_DEFER: "🟡 Отложено",
    scheduler.ACTION_BLOCKED: "🔴 Заблокировано",
    task_pipeline.ACTION_SKIPPED: "⚪️ Пропущено",
}

_STUCK_KIND_LABELS: dict[str, str] = {
    task_pipeline.STUCK_KIND_COMPLETION: "конвейер остановлен",
    task_pipeline.STUCK_KIND_LAUNCH: "последний прогон завершился ошибкой",
    task_pipeline.STUCK_KIND_NOT_STARTING: "не стартует",
    task_pipeline.STUCK_KIND_REWORK_EXHAUSTED: "исчерпан бюджет доработок",
}

_STATUS_LABELS: dict[str, str] = {
    task_pipeline.TICK_DISABLED: "Автопилот выключен",
    task_pipeline.TICK_BUSY: "Тик пропущен — конвейер занят другим процессом",
    task_pipeline.TICK_RAN: "Тик выполнен",
}


def _short(path: str | None, *, keep: int = 2) -> str:
    """The last `keep` path segments — a workspace column that stays readable in
    a narrow caption without hiding which worktree it is."""
    if not path:
        return "—"
    parts = Path(path).parts
    return "…/" + "/".join(parts[-keep:]) if len(parts) > keep else path


def render_autopilot_controls(root: Path, *, key_prefix: str = "autopilot") -> pipeline_settings.PipelineSettings:
    """The persistent opt-in surface (AICC-DESKTOP-015). Every control defaults
    to off and reflects what is actually on disk, so the displayed state is the
    machine's real permission, not this browser tab's memory of it."""
    settings = pipeline_settings.load_settings(root)

    st.markdown("#### Автопилот")
    st.caption(
        "Автопилот запускает задачи из очереди и ведёт их до merge без ручного нажатия. "
        "Все переключатели выключены по умолчанию и сохраняются на диск."
    )

    columns = st.columns(2)
    with columns[0]:
        enabled = st.toggle(
            "Включить автопилот",
            value=settings.enabled,
            key=f"{key_prefix}_enabled",
            help="Главный выключатель. Пока он выключен, ни один другой переключатель ничего не делает.",
        )
        auto_launch = st.toggle(
            "Автозапуск готовых задач",
            value=settings.auto_launch,
            key=f"{key_prefix}_auto_launch",
            disabled=not enabled,
            help="Запускать задачи, которые планировщик пометил ASSIGN. Без него волна только показывается.",
        )
        auto_rework = st.toggle(
            "Автодоработка при провале проверки",
            value=settings.auto_rework,
            key=f"{key_prefix}_auto_rework",
            disabled=not (enabled and auto_launch),
            help=(
                "Если проверка не прошла — перезапустить ту же задачу новой попыткой, "
                "передав агенту вывод упавшей проверки. Доработка — это запуск, поэтому "
                "требует включённого автозапуска."
            ),
        )
        auto_remediate = st.toggle(
            "Автоочистка своих worktree",
            value=settings.auto_remediate_workspace,
            key=f"{key_prefix}_auto_remediate",
            disabled=not (enabled and auto_launch),
            help=(
                "Убирает остатки прошлой попытки в git stash, если workspace — изолированный "
                "worktree проекта, созданный конвейером. Работа восстановима: git stash pop. "
                "Основное рабочее дерево и чужие репозитории не трогаются никогда, "
                "reset --hard и clean не выполняются ни при каких настройках."
            ),
        )
        max_rework = st.number_input(
            "Попыток доработки",
            min_value=pipeline_settings.MIN_REWORK_ATTEMPTS,
            max_value=pipeline_settings.MAX_REWORK_ATTEMPTS,
            value=settings.max_rework_attempts,
            step=1,
            key=f"{key_prefix}_max_rework",
            disabled=not (enabled and auto_launch),
            help="Исчерпав бюджет, задача остаётся человеку с записанной причиной.",
        )
    with columns[1]:
        auto_merge = st.toggle(
            "Автоматический merge после проверок",
            value=settings.auto_merge_after_checks,
            key=f"{key_prefix}_auto_merge",
            disabled=not enabled,
            help=(
                "Разрешает политику auto_after_checks. Сам merge всё равно происходит только когда "
                "проверки пройдены, ревью (если требуется) получено и PR технически мержится."
            ),
        )
        require_review = st.toggle(
            "Независимая проверка перед PR",
            value=settings.require_independent_review,
            key=f"{key_prefix}_require_review",
            disabled=not enabled,
            help=(
                "Блокирующий гейт: отдельный ревьюер в режиме только для чтения "
                "(без Bash и редактирования — он физически не может править код) "
                "читает диф и выносит вердикт. Не одобрил — PR не создаётся, "
                "задача уходит в доработку."
            ),
        )
        limits = st.columns(2)
        with limits[0]:
            max_global = st.number_input(
                "Всего параллельно",
                min_value=pipeline_settings.MIN_CONCURRENCY,
                max_value=pipeline_settings.MAX_CONCURRENCY,
                value=settings.max_global_concurrency,
                step=1,
                key=f"{key_prefix}_max_global",
            )
        with limits[1]:
            max_agent = st.number_input(
                "На агента",
                min_value=pipeline_settings.MIN_CONCURRENCY,
                max_value=pipeline_settings.MAX_CONCURRENCY,
                value=settings.max_agent_concurrency,
                step=1,
                key=f"{key_prefix}_max_agent",
            )

    changes = {
        "enabled": bool(enabled),
        "auto_launch": bool(auto_launch),
        "auto_merge_after_checks": bool(auto_merge),
        "auto_rework": bool(auto_rework),
        "auto_remediate_workspace": bool(auto_remediate),
        "require_independent_review": bool(require_review),
        "max_global_concurrency": int(max_global),
        "max_agent_concurrency": int(max_agent),
        "max_rework_attempts": int(max_rework),
    }
    if changes != {k: v for k, v in settings.as_dict().items() if k in changes}:
        settings = pipeline_settings.update_settings(root, actor="desktop_ui", **changes)

    if settings.enabled and settings.auto_launch:
        st.warning(
            "Автозапуск активен: задачи из очереди будут стартовать без подтверждения. "
            "Проверки workspace и подтверждение чувствительного контента при этом не отключаются.",
            icon=":material/bolt:",
        )
    if settings.updated_at:
        st.caption(f"Изменено: {settings.updated_at} · {settings.updated_by or '—'}")
    return settings


def _render_decisions(decisions, *, empty_caption: str) -> None:
    """One row per decision, with priority, assigned agent, workspace, the
    planner's own rationale and — for anything that did not start — the
    machine-readable reason plus its remediation (AICC-DESKTOP-013/014)."""
    if not decisions:
        st.caption(empty_caption)
        return

    for decision in decisions:
        with st.container(border=True):
            header = st.columns([3, 1, 1, 1])
            header[0].markdown(f"**{decision.title or decision.task_id or '—'}**")
            header[1].caption(_ACTION_LABELS.get(decision.action, decision.action))
            header[2].caption(f"приоритет {decision.priority or '—'}")
            header[3].caption(decision.agent_id or "—")

            meta = st.columns([2, 1, 1])
            meta[0].caption(f"workspace: `{_short(decision.workspace)}`")
            meta[1].caption(f"проект: {decision.project or '—'}")
            meta[2].caption(
                f"попытка {decision.attempt}" if decision.attempt else "попытка —"
            )

            st.caption(f"Почему: {decision.explanation}")
            if decision.next_eligible_at:
                st.caption(f"Следующая попытка не раньше: {decision.next_eligible_at}")

            if decision.launched:
                st.caption(f"✅ Запущено · прогон `{(decision.run_id or '')[:8]}`")
                continue

            # Did not start — always show the code and what to do about it.
            code = decision.launch_reason_code or decision.reason_code
            st.caption(f"Код: `{code}`")
            if decision.launch_message:
                st.caption(decision.launch_message)
            if decision.warnings:
                for warning in decision.warnings:
                    st.caption(f"• {warning}")
            remediation = decision.remediation
            if remediation:
                st.caption(f"Что делать: {remediation}")


def render_autopilot_panel(root: Path, *, key_prefix: str = "autopilot") -> None:
    """Controls plus the wave, for callers that render both in one place."""
    render_autopilot_controls(root, key_prefix=key_prefix)
    render_autopilot_wave()


def render_autopilot_wave(result=None) -> None:
    """The wave and the last tick's durable outcome.

    Separate from the controls because the two have opposite refresh needs: the
    toggles must keep a stable identity and position across reruns, while the
    wave is only meaningful if it re-renders with every tick. Rendering both
    outside the auto-refresh fragment left the wave frozen at whatever existed
    on first load — which, on a fresh session, is nothing at all.

    `result` may be passed directly by the refresh path that just computed it;
    otherwise the stashed one is used."""
    if result is None:
        result = st.session_state.get(TICK_RESULT_KEY)

    failure = st.session_state.pop(TICK_ERROR_KEY, None)
    if failure:
        st.error(f"Тик автопилота не выполнен: {failure}", icon=":material/error:")

    if result is None:
        st.caption("Пока нет данных: волна появится после первого обновления Live Execution Center.")
        return

    st.divider()
    status_line = _STATUS_LABELS.get(result.status, result.status)
    if not result.ran:
        st.info(f"{status_line}.")
    else:
        summary = [
            f"запущено: {len(result.launched())}",
            f"решений: {len(result.decisions)}",
        ]
        if result.completed_task_ids:
            summary.append(f"закрыто задач: {len(result.completed_task_ids)}")
        if result.merge_policy_updates:
            summary.append(f"политика merge обновлена: {len(result.merge_policy_updates)}")
        if result.reworks:
            summary.append(f"доработок назначено: {len(result.reworks)}")
        if result.remediations:
            summary.append(f"workspace прибрано: {len(result.remediations)}")
        if result.stuck:
            summary.append(f"остановлено: {len(result.stuck)}")
        st.caption(f"{status_line} · " + " · ".join(summary))

    if result.launch_status == task_pipeline.LAUNCH_DISABLED and result.ran:
        st.caption("Автозапуск выключен — волна показана, но ничего не запускалось.")
    elif result.launch_status.startswith(task_pipeline.LAUNCH_BATCH_FAILED):
        st.error(f"Пакет запуска не выполнен: {result.launch_status}", icon=":material/error:")

    st.markdown("##### Текущая волна")
    _render_decisions(result.decisions, empty_caption="Нет задач, готовых к планированию.")

    if result.next_wave:
        st.markdown("##### Следующая волна")
        _render_decisions(result.next_wave, empty_caption="—")

    if result.completed_task_ids:
        st.success(
            "Merge подтверждён в целевой ветке, задачи переведены в Done: "
            + ", ".join(result.completed_task_ids),
            icon=":material/check_circle:",
        )

    requeued = [r for r in result.reworks if r["outcome"] == task_pipeline.REWORK_REQUEUED]
    exhausted = [r for r in result.reworks if r["outcome"] == task_pipeline.REWORK_BUDGET_EXHAUSTED]
    if requeued:
        st.info(
            "Проверка не прошла — назначена доработка: "
            + ", ".join(f"{r['task_id']} (попытка {r['attempts']})" for r in requeued),
            icon=":material/refresh:",
        )
    if exhausted:
        st.warning(
            "Исчерпан бюджет доработок, задача остаётся человеку: "
            + ", ".join(r["task_id"] for r in exhausted),
            icon=":material/report:",
        )

    requested = [r for r in result.reviews if r["outcome"] == task_pipeline.REVIEW_REQUESTED]
    recorded = [r for r in result.reviews if r["outcome"] == task_pipeline.REVIEW_RECORDED]
    if requested:
        st.info(
            "Запущена независимая проверка: " + ", ".join(r["task_id"] for r in requested),
            icon=":material/rate_review:",
        )
    for record in recorded:
        if record.get("approved"):
            st.success(
                f"Независимая проверка одобрила {record['task_id']} — можно открывать PR.",
                icon=":material/verified:",
            )
        else:
            st.warning(
                f"Независимая проверка не одобрила {record['task_id']}: {record.get('detail') or '—'}",
                icon=":material/gpp_maybe:",
            )

    stashed = [r for r in result.remediations if r["outcome"] == task_pipeline.REMEDIATION_STASHED]
    if stashed:
        st.info(
            "Остатки прошлых попыток убраны в stash (восстановить: `git stash pop`): "
            + ", ".join(r["task_id"] for r in stashed),
            icon=":material/inventory_2:",
        )

    # Nothing silently unfinished: a task that has stopped without reaching Done
    # is reported here every tick, with the reason and what to do about it.
    if result.stuck:
        st.error(
            f"Задачи, остановившиеся без завершения: {len(result.stuck)}. "
            "Каждая ниже — с причиной и что с ней делать.",
            icon=":material/pending_actions:",
        )
        for item in result.stuck:
            with st.container(border=True):
                st.markdown(f"**{item.title or item.task_id}** · {_STUCK_KIND_LABELS.get(item.kind, item.kind)}")
                st.caption(f"{item.project or '—'} · код: `{item.reason_code}`")
                if item.detail:
                    st.caption(item.detail)
                if item.remediation:
                    st.caption(f"Что делать: {item.remediation}")

    if result.completion_advances:
        with st.expander(f"Прогресс завершения ({len(result.completion_advances)})"):
            for advance in result.completion_advances:
                arrow = f"{advance['from_state']} → {advance['to_state']}"
                st.caption(f"`{advance['run_id'][:8]}` {arrow} · {advance['reason_code']}")

    if result.errors:
        with st.expander(f"Ошибки тика ({len(result.errors)})"):
            for error in result.errors:
                st.caption(f"• {error}")
