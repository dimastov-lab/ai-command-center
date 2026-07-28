"""Portfolio Overview panel: a read-only, evidence-backed view of the whole
Portfolio — per-project health, cross-project dependency waves, the critical
path, blocked/at-risk work, capacity/allocation demand, and deterministic
recommendations.

This panel never mutates anything: no launch, no branch/worktree creation, no
config write. Those all live in `command_center.ui.portfolio_panel`
(Portfolio Execution). This surface only renders the plain-data
`PortfolioOverview` computed by `command_center.portfolio_intelligence`, so
the domain logic stays independent of Streamlit and this file stays a thin
presentation boundary (same split as
`command_center.ui.project_intelligence_panel`).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from command_center import portfolio_config, portfolio_intelligence, portfolio_launch
from command_center.portfolio_models import load_portfolio_tasks
from command_center.ui.portfolio_panel import PORTFOLIO_ROOT_ENV, portfolio_root

_HEALTH_COLOR = {
    portfolio_intelligence.HEALTH_GOOD: "green",
    portfolio_intelligence.HEALTH_ATTENTION: "orange",
    portfolio_intelligence.HEALTH_AT_RISK: "red",
}

_READINESS_LABEL = {
    portfolio_intelligence.READINESS_READY: ("Готова", "green"),
    portfolio_intelligence.READINESS_LAUNCHED: ("Запущена", "blue"),
    portfolio_intelligence.READINESS_BLOCKED: ("Заблокирована", "orange"),
    portfolio_intelligence.READINESS_GATED: ("Гейт", "violet"),
    portfolio_intelligence.READINESS_UNMAPPED: ("Нет репозитория", "red"),
    portfolio_intelligence.READINESS_NOT_READY_LANE: ("Не в ready", "gray"),
}


def _render_projects(overview: portfolio_intelligence.PortfolioOverview) -> None:
    st.markdown("##### Здоровье проектов")
    for project in overview.projects:
        color = _HEALTH_COLOR.get(project.health, "gray")
        cols = st.columns([2, 1, 3])
        cols[0].markdown(f"**{project.project}**")
        cols[1].badge(project.health, color=color)
        cols[2].caption(project.health_reason)
        lanes = " · ".join(f"{lane}: {count}" for lane, count in sorted(project.by_lane.items()))
        st.caption(
            f"repo: `{project.repository_path or '— не сопоставлен —'}` · всего {project.total} · "
            f"готовы {project.ready} · заблокированы {project.blocked} · гейт {project.gated} · {lanes}"
        )
        if project.evidence:
            with st.expander("Обоснование", expanded=False):
                for item in project.evidence:
                    st.write(f"- {item}")


def _render_waves(overview: portfolio_intelligence.PortfolioOverview) -> None:
    graph = overview.graph
    st.markdown("##### Волны готовности (roadmap)")
    st.caption(
        "Волна 0 — задачи без незакрытых зависимостей; каждая следующая волна зависит от предыдущей."
    )
    if not graph.waves:
        st.info("Нет задач для построения волн.")
    for index, wave in enumerate(graph.waves):
        st.write(f"**Волна {index}** — {', '.join(f'`{tid}`' for tid in wave)}")
    if graph.cyclic_task_ids:
        st.error(
            "Цикл зависимостей (эти задачи не попадают ни в одну волну): "
            + ", ".join(f"`{tid}`" for tid in graph.cyclic_task_ids)
        )
    if graph.critical_path:
        st.markdown("**Критический путь** (самая длинная цепочка зависимостей):")
        st.write(" → ".join(f"`{tid}`" for tid in graph.critical_path))
    if graph.unresolved_requires:
        with st.expander(f"Зависимости вне загруженных дорожек ({len(graph.unresolved_requires)})"):
            st.caption("Считаются уже выполненными — этот модуль не загружает done/archive.")
            for task_id, dep_id in graph.unresolved_requires:
                st.write(f"- `{task_id}` requires `{dep_id}` (не найдена среди открытых задач)")


def _render_capacity(overview: portfolio_intelligence.PortfolioOverview) -> None:
    capacity = overview.capacity
    st.markdown("##### Спрос на ресурсы и аллокация")
    st.caption(f"Готовы к немедленному запуску: {capacity.ready_count}")
    if capacity.by_repository:
        st.write("**По репозиториям:** " + ", ".join(f"`{path}` × {n}" for path, n in capacity.by_repository))
    if capacity.agents_requested:
        st.write("**По агентам:** " + ", ".join(f"{agent} × {n}" for agent, n in capacity.agents_requested))
    if capacity.by_parallel_group:
        for group, ids in capacity.by_parallel_group:
            st.write(f"**Параллельная группа `{group}`:** " + ", ".join(f"`{t}`" for t in ids))
    for path, ids in capacity.worktree_collisions:
        st.warning(f"Коллизия worktree `{path}`: " + ", ".join(f"`{t}`" for t in ids))
    for a, b in capacity.conflict_pairs:
        st.warning(f"Конфликт: `{a}` ↔ `{b}` (объявлены взаимоисключающими)")
    if capacity.unmapped_projects:
        st.info("Проекты без локального репозитория: " + ", ".join(f"`{p}`" for p in capacity.unmapped_projects))


def _render_recommendations(overview: portfolio_intelligence.PortfolioOverview) -> None:
    st.markdown("##### Рекомендации портфеля")
    if not overview.recommendations:
        st.caption("Нет рекомендаций.")
        return
    for rec in overview.recommendations:
        with st.container(border=True):
            st.markdown(f"**{rec.summary}**")
            if rec.task_ids:
                st.caption("Задачи: " + ", ".join(f"`{t}`" for t in rec.task_ids))
            with st.expander("Доказательства"):
                for item in rec.evidence:
                    st.write(f"- {item}")


def _render_tasks(overview: portfolio_intelligence.PortfolioOverview) -> None:
    st.markdown("##### Задачи и готовность к исполнению")
    for row in overview.tasks:
        label, color = _READINESS_LABEL.get(row.readiness, (row.readiness, "gray"))
        with st.container(border=True):
            header = st.columns([3, 1, 1])
            header[0].markdown(f"**`{row.task_id}`** — {row.title or '(без названия)'}")
            header[1].badge(label, color=color)
            header[2].caption(f"волна {row.wave if row.wave is not None else '—'}")
            st.caption(
                f"проект `{row.project}` · дорожка `{row.lane}` · приоритет `{row.priority}` · "
                f"repo `{row.repository_path or '—'}` · карточка `{row.source_path}`"
                + (f" · сессия `{row.run_id}`" if row.run_id else "")
            )
            if row.blockers:
                for blocker in row.blockers:
                    st.write(f"- ⛔ {blocker}")
            if row.evidence:
                for item in row.evidence:
                    st.write(f"- ✅ {item}")


def render_portfolio_overview_panel(*, root: Path) -> None:
    st.markdown("#### Portfolio Overview")
    st.caption(
        "Детерминированный, доказуемый обзор портфеля: здоровье, зависимости, волны, "
        "критический путь, спрос на ресурсы и рекомендации. Источник — файловые "
        "roadmap-карточки Portfolio, а не оперативный Kanban; поэтому эти числа "
        "не смешиваются со счётчиками задач Live Center. Только чтение."
    )

    p_root = portfolio_root()
    result = load_portfolio_tasks(p_root)
    repository_paths = portfolio_config.load_repository_paths()
    registry = portfolio_launch.load_registry(root)
    overview = portfolio_intelligence.compute_portfolio_overview(
        result, repository_paths=repository_paths, registry=registry
    )

    if overview.missing:
        st.error(
            f"Portfolio не найден по пути: `{p_root}`. Настройте `{PORTFOLIO_ROOT_ENV}` или создайте checkout."
        )
        return

    if overview.card_issues:
        with st.expander(f"Проблемы с карточками ({len(overview.card_issues)})", expanded=False):
            for issue in overview.card_issues:
                st.warning(f"`{issue.source_path}`: {issue.message}")

    top = st.columns(4)
    top[0].metric("Roadmap-карточек", overview.total_tasks)
    top[1].metric("Готовы по roadmap", len(overview.ready_now))
    top[2].metric("Заблокированы в roadmap", len(overview.blocked))
    top[3].metric("Roadmap-проекты в риске", len(overview.at_risk_projects))

    if overview.total_tasks == 0:
        st.info("В загруженных дорожках нет задач.")
        return

    st.divider()
    _render_recommendations(overview)
    st.divider()
    _render_projects(overview)
    st.divider()
    _render_waves(overview)
    st.divider()
    _render_capacity(overview)
    st.divider()
    _render_tasks(overview)
