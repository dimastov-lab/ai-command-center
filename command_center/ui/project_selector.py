"""The canonical project selector — one control, backed by
`models.PROJECT_IDS` (the single source of truth for which projects exist),
never a second hand-maintained project list. Every registered project is
always offered; the three most active are only ever visually grouped first,
never hidden — see `command_center.project_intelligence.rank_projects_by_activity`.
"""

from __future__ import annotations

import streamlit as st

from command_center import models, project_config, project_intelligence

ALL_PROJECTS_LABEL = "Все проекты"
TOP_GROUP_SIZE = 3


def render_project_selector(tasks: list[dict], *, key: str = "project_selector") -> str | None:
    """Renders a pill-style selector covering every id in `models.PROJECT_IDS`
    plus an "All projects" option, and returns the selected project id (or
    `None` for "All projects"). The top `TOP_GROUP_SIZE` projects by active
    task count are listed first — a display-order hint only; every project
    still appears."""
    ranked = project_intelligence.rank_projects_by_activity(models.PROJECT_IDS, tasks)
    top_group = ranked[:TOP_GROUP_SIZE]
    rest_group = sorted((p for p in models.PROJECT_IDS if p not in top_group), key=models.PROJECT_IDS.index)
    options = [ALL_PROJECTS_LABEL, *top_group, *rest_group]

    counts = {project_id: 0 for project_id in models.PROJECT_IDS}
    for task in tasks:
        project = task.get("project")
        if project in counts:
            counts[project] += 1

    def _format(option: str) -> str:
        if option == ALL_PROJECTS_LABEL:
            return f"{ALL_PROJECTS_LABEL} · {len(tasks)}"
        name = project_config.DISPLAY_NAMES.get(option, option)
        return f"{name} · {counts.get(option, 0)}"

    selected = st.pills(
        "Проект",
        options,
        default=ALL_PROJECTS_LABEL,
        required=True,
        format_func=_format,
        key=f"{key}_pills",
    )
    return None if selected == ALL_PROJECTS_LABEL else selected
