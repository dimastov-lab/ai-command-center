"""Sidebar navigation component (UX-1 AppShell)."""

from __future__ import annotations

from typing import Callable

import streamlit as st


def render_sidebar(
    nav: dict[str, tuple[str, str]],
    *,
    project_count: int,
    on_open_palette: Callable[[], None],
) -> str:
    """Render the primary navigation sidebar and return the active page key.

    Behavior-preserving extraction of app.py's former inline `with
    st.sidebar:` block. The nav radio keeps its `nav_page` session-state
    key exactly, since tests drive navigation by setting it directly
    (`tests/test_app_streamlit.py`'s `_at_on_page`).
    """
    with st.sidebar:
        st.button(
            "Командная палитра (Mod+K)",
            icon=":material/search:",
            shortcut="Mod+K",
            on_click=on_open_palette,
            width="stretch",
            key="open_palette_btn",
        )
        st.divider()
        st.markdown("### Навигация")
        page_key = st.radio(
            "Раздел",
            options=list(nav.keys()),
            format_func=lambda key: f"{nav[key][1]} {nav[key][0]}",
            label_visibility="collapsed",
            key="nav_page",
        )
        st.divider()
        st.caption(f"Проектов в реестре: {project_count}")
        st.caption("Локальный режим · без внешних сервисов")

    return page_key
