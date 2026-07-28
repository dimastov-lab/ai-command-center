"""Top command bar component (UX-1 AppShell, extended UX-2c).

Hosts the page-level title/caption, command-palette trigger, live execution
status glyph, and Inspector pane as a popover
(`command_center/ui/inspector.py`). A popover (rather than a persistent column
split) means mounting the Inspector adds no layout change to any existing
page's content — it only ever appears on demand, anchored from the top bar
itself.
"""

from __future__ import annotations

import streamlit as st

from command_center.runtime import db as runtime_db
from command_center.ui import inspector


def _live_status_glyph(api) -> str:
    """Compact ``⏺N ⏸M ⚠K`` summary of live execution — the one piece of
    cross-page context an operator glances at the top bar for. Cheap: three
    ``count_runs`` queries, no row materialization."""
    if api is None:
        return ""
    running = api.count_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES)
    waiting = api.count_runs(states=["QUEUED", "PREPARED"])
    failed = api.count_runs(states=["FAILED"])
    parts = []
    if running:
        parts.append(f"⏺ {running}")
    if waiting:
        parts.append(f"⏸ {waiting}")
    if failed:
        parts.append(f"⚠ {failed}")
    return " · ".join(parts)


def render_top_bar(
    title: str,
    caption: str,
    *,
    on_open_palette,
    tasks_by_id: dict[str, dict] | None = None,
    api=None,
) -> None:
    header_col, status_col, search_col, inspector_col = st.columns(
        [5, 2, 1, 1],
        vertical_alignment="bottom",
    )
    with header_col:
        st.title(title)
        st.caption(caption)
    with status_col:
        glyph = _live_status_glyph(api)
        if glyph:
            st.markdown(f"###### {glyph}")
    with search_col:
        st.button(
            "Поиск",
            icon=":material/search:",
            shortcut="Mod+K",
            on_click=on_open_palette,
            width="stretch",
            key="top_open_palette_btn",
        )
    with inspector_col:
        label = inspector.current_label(tasks_by_id or {})
        with st.popover(label, icon=":material/right_panel_open:", width="stretch"):
            if tasks_by_id is not None and api is not None:
                inspector.render_inspector(tasks_by_id=tasks_by_id, api=api)
            else:
                inspector.render_placeholder()
