"""Top command bar component (UX-1 AppShell).

Hosts the page-level title/caption plus one reserved control: a popover
exposing the Inspector placeholder panel (`command_center/ui/inspector.py`).
A popover (rather than a persistent column split) means mounting the
Inspector adds no layout change to any existing page's content — it only
ever appears on demand, anchored from the top bar itself.
"""

from __future__ import annotations

import streamlit as st

from command_center.ui import inspector


def render_top_bar(title: str, caption: str) -> None:
    header_col, inspector_col = st.columns([6, 1], vertical_alignment="bottom")
    with header_col:
        st.title(title)
        st.caption(caption)
    with inspector_col:
        with st.popover("Инспектор", icon=":material/right_panel_open:", width="stretch"):
            inspector.render_placeholder()
