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

from command_center.ui import execution_strip, inspector


def _live_status_glyph(api, tasks_by_id) -> str:
    """Compact ``⏺N ⏸M ⚠K`` summary of live execution — the one piece of
    cross-page context an operator glances at the top bar for. Uses the exact
    same `execution_strip.current_counts` the strip banner and the Live Center
    headline use — the *actionable* count, which drops superseded, completed-task
    and operator-dismissed rows — so ⚠ shows one canonical number across the
    strip, the AI-Supervisor caption and this glyph (audit D5). Attention/running
    do not depend on the durable queue, so no queue load is needed here."""
    if api is None:
        return ""
    counts = execution_strip.current_counts(
        api.list_runs(limit=200),
        list((tasks_by_id or {}).values()),
        [],
        dismissed_attention_run_ids=st.session_state.get("exec_attention_dismissed", set()),
    )
    parts = []
    if counts.live:
        parts.append(f"⏺ {counts.live}")
    if counts.waiting:
        parts.append(f"⏸ {counts.waiting}")
    if counts.attention:
        parts.append(f"⚠ {counts.attention}")
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
        glyph = _live_status_glyph(api, tasks_by_id)
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
