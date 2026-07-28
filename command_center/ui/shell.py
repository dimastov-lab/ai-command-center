"""AppShell (UX-1): composes page config, TopCommandBar, and Sidebar into
one entry point so `app.py` stays thin. Content Area/Inspector are handled
separately by `command_center.ui.content_area` around the page-routing
dispatch, since that dispatch is per-page, not shell-level.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from command_center.ui import sidebar, theme, top_bar


def render_shell(
    *,
    page_title: str,
    page_icon: str,
    sidebar_collapsed: bool,
    title: str,
    caption: str,
    nav: dict[str, tuple[str, str]],
    project_count: int,
    on_open_palette: Callable[[], None],
) -> str:
    """Render page config, the top command bar, and the sidebar.

    Returns the active page key (`nav_page` session-state value).
    """
    st.set_page_config(
        page_title=page_title,
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="collapsed" if sidebar_collapsed else "expanded",
    )

    # App-wide CSS (UX-2a): fragment fade-in + card hover transitions. Emitted
    # every run so it survives reruns (see theme.inject_global_css docstring).
    theme.inject_global_css()

    top_bar.render_top_bar(title, caption, on_open_palette=on_open_palette)

    return sidebar.render_sidebar(nav, project_count=project_count, on_open_palette=on_open_palette)
