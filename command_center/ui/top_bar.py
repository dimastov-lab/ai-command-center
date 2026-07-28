"""Top command bar component."""

from __future__ import annotations

import streamlit as st

def render_top_bar(title: str, caption: str, *, on_open_palette) -> None:
    header_col, action_col = st.columns([6, 1], vertical_alignment="bottom")
    with header_col:
        st.title(title)
        st.caption(caption)
    with action_col:
        st.button(
            "Поиск",
            icon=":material/search:",
            shortcut="Mod+K",
            on_click=on_open_palette,
            width="stretch",
            key="top_open_palette_btn",
        )
