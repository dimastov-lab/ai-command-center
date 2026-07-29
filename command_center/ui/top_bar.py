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

from command_center import read_model
from command_center.ui import inspector


def _live_status_glyph(api) -> str:
    """Compact ``⏺N ⏸M ⚠K`` summary of live execution — the one piece of
    cross-page context an operator glances at the top bar for. Counts the
    *non-superseded* latest attempt of each task through the shared read-model,
    so ⚠ agrees with the Execution Strip and the AI-Supervisor caption instead of
    showing an all-time FAILED tally that overstated attention (audit D5). One
    bounded `list_runs` replaces the three all-time `count_runs` queries."""
    if api is None:
        return ""
    runs = api.list_runs(limit=200)
    live = [r for r in runs if r.get("id") not in read_model.superseded_run_ids(runs)]
    snapshot = read_model.run_snapshot(live)
    parts = []
    if snapshot.running:
        parts.append(f"⏺ {snapshot.running}")
    if snapshot.queued:
        parts.append(f"⏸ {snapshot.queued}")
    if snapshot.attention:
        parts.append(f"⚠ {snapshot.attention}")
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
