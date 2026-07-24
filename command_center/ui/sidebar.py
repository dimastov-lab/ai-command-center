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
        # Session state does not survive a browser refresh — a reload starts a
        # new session — so the section would silently reset to the first option
        # every time. The URL does survive, so it is the honest place to keep
        # "which section am I in". Seeded before the radio is created, because
        # Streamlit refuses to reassign a widget's key once the widget exists.
        if "nav_page" not in st.session_state:
            requested = st.query_params.get("page")
            if requested in nav:
                st.session_state["nav_page"] = requested

        page_key = st.radio(
            "Раздел",
            options=list(nav.keys()),
            format_func=lambda key: f"{nav[key][1]} {nav[key][0]}",
            label_visibility="collapsed",
            key="nav_page",
        )
        # Mirror the section back into the URL so a refresh — or a copied link —
        # reopens where the operator actually was.
        if st.query_params.get("page") != page_key:
            st.query_params["page"] = page_key

        st.divider()
        st.caption(f"Проектов в реестре: {project_count}")
        st.caption("Локальный режим · без внешних сервисов")

    return page_key
