"""Renders the Project Intelligence KPI strip — health, sprint/roadmap
progress, remaining/blocked work, and the critical path — over
`command_center.project_intelligence`'s computed (never persisted) rollup."""

from __future__ import annotations

import streamlit as st

from command_center import project_intelligence

_HEALTH_ICONS: dict[str, str] = {
    project_intelligence.HEALTH_GOOD: "🟢",
    project_intelligence.HEALTH_ATTENTION: "🟡",
    project_intelligence.HEALTH_AT_RISK: "🔴",
}


def render_project_intelligence_strip(tasks: list[dict], *, project: str | None = None) -> None:
    intel = project_intelligence.compute_project_intelligence(project, tasks)

    cols = st.columns(6)
    cols[0].metric(
        "Здоровье",
        f"{_HEALTH_ICONS.get(intel['health'], '')} {intel['health']}",
        help=intel["health_reason"] or None,
        border=True,
    )
    sprint_value = f"{intel['sprint_progress_pct']}%" if intel["sprint_progress_pct"] is not None else "—"
    cols[1].metric(
        "Прогресс спринта",
        sprint_value,
        help="Средний прогресс активных задач — на уровне задачи нет привязки к конкретному спринту.",
        border=True,
    )
    roadmap_value = f"{intel['roadmap_progress_pct']}%" if intel["roadmap_progress_pct"] is not None else "—"
    cols[2].metric("Roadmap", roadmap_value, border=True)
    cols[3].metric("Осталось", intel["remaining"], border=True)
    cols[4].metric("Заблокировано", intel["blocked"], border=True)
    completion_value = f"{intel['completion_pct']}%" if intel["completion_pct"] is not None else "—"
    cols[5].metric("Завершено", completion_value, border=True)

    if intel["critical_path"]:
        st.caption("Критический путь: " + " → ".join(intel["critical_path"]))
