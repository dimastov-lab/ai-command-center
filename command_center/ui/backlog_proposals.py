"""Turn an agent's markdown report into a reviewable list of candidate backlog
tasks — shared by the project Audit (d58b56f3) and Roadmap reformat (63633657)
buttons.

An agent (architecture/rules/quality/UX audit, or a roadmap rebuild) writes a
markdown report. Somewhere in it a section lists the work it recommends; this
module extracts those bullets into `CandidateTask`s and renders them with a
per-row **Применить** (create the task) / **Отклонить** (dismiss for this
session). Nothing is created until the operator presses Применить.

Parsing is deliberately forgiving because the section heading and bullet shape
vary a little between prompts: any heading whose text mentions предлаг/задач/
backlog/roadmap/recommend/proposed opens the list, and each `-`/`*`/numbered
bullet under it becomes one candidate. `**Title** — goal`, `Title — goal`,
`Title: goal` and a bare `Title` are all understood.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from command_center import tasks_repository

# A heading opens the candidate list when its text mentions any of these.
_SECTION_HINTS = ("предлаг", "задач", "backlog", "roadmap", "recommend", "proposed", "предложен")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
# "**Title** — goal" | "Title — goal" | "Title: goal"  (— / – / - / : as splitter)
_TITLE_GOAL_RE = re.compile(r"^\s*(?:\*\*(?P<b>.+?)\*\*|(?P<t>.+?))\s*(?:[—–:-]\s+(?P<g>.+))?$")


@dataclass(frozen=True)
class CandidateTask:
    """One proposed task parsed from an agent report."""

    title: str
    goal: str
    task_type: str = "implementation"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", text or "")).strip()


def parse_candidate_tasks(markdown: str, *, default_task_type: str = "implementation") -> list[CandidateTask]:
    """Extract candidate tasks from the first report section that reads like a
    list of recommended work. Returns [] when no such section/bullets exist."""
    if not markdown:
        return []
    lines = markdown.splitlines()
    in_section = False
    candidates: list[CandidateTask] = []
    seen: set[str] = set()
    for line in lines:
        heading = _HEADING_RE.match(line)
        if heading:
            text = heading.group(1).lower()
            in_section = any(hint in text for hint in _SECTION_HINTS)
            continue
        if not in_section:
            continue
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        match = _TITLE_GOAL_RE.match(bullet.group(1))
        if not match:
            continue
        title = _clean(match.group("b") or match.group("t") or "")
        goal = _clean(match.group("g") or "") or title
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        candidates.append(CandidateTask(title=title[:120], goal=goal, task_type=default_task_type))
    return candidates


def render_candidate_tasks(
    candidates: list[CandidateTask],
    root: Path,
    project: str,
    *,
    key_prefix: str,
    heading: str = "Предлагаемые задачи",
) -> None:
    """Render parsed candidates with per-row Применить (creates the task in
    Backlog) / Отклонить (hides it for this session)."""
    dismissed_key = f"{key_prefix}_dismissed"
    dismissed: set[str] = st.session_state.setdefault(dismissed_key, set())
    visible = [c for c in candidates if c.title not in dismissed]

    st.markdown(f"##### {heading} · {len(visible)}")
    if not visible:
        st.caption("Кандидатов нет — запустите анализ, чтобы получить предложения.")
        return

    for index, candidate in enumerate(visible):
        info, apply_col, skip_col = st.columns([0.64, 0.18, 0.18])
        with info:
            st.markdown(f"**{candidate.title}**")
            if candidate.goal and candidate.goal != candidate.title:
                st.caption(candidate.goal)
        applied = apply_col.button(
            "Применить", key=f"{key_prefix}_apply_{index}", type="primary", use_container_width=True
        )
        skipped = skip_col.button("Отклонить", key=f"{key_prefix}_skip_{index}", use_container_width=True)
        if applied:
            tasks_repository.create_task(
                root, project, candidate.title, candidate.task_type, "Backlog", goal=candidate.goal
            )
            dismissed.add(candidate.title)
            st.session_state[dismissed_key] = dismissed
            st.toast(f"Создана задача: {candidate.title}")
            st.rerun()
        if skipped:
            dismissed.add(candidate.title)
            st.session_state[dismissed_key] = dismissed
            st.rerun()
