"""Sidebar navigation (UX-1 AppShell).

Nineteen sections in one flat radio list is a directory listing, not
navigation: everything is equally prominent, so nothing is, and finding a
screen means reading every label. Sections are grouped here by *what the
operator is doing* — planning work, watching it execute, managing projects,
everything consulted occasionally — with the two daily-use groups open by
default and the rest collapsed.

Grouping is presentation only. `nav_page` keeps its exact session-state key and
its values, because tests drive navigation by setting it directly and the URL
carries it; regrouping must not invalidate a bookmarked link.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

# (title, page keys, open by default). Ordered like a working day: decide what
# to do, watch it happen, then the things consulted occasionally.
NAV_GROUPS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("Работа", ("dashboard", "kanban", "waves", "create"), True),
    ("Исполнение", ("execution_center", "runs", "reports", "agents"), True),
    ("Проекты", ("projects", "portfolio", "portfolio_overview", "workspace_home"), False),
    ("Анализ", ("executive", "timeline", "generated", "context"), False),
    ("Инструменты", ("git_center", "workspace", "chat", "focus"), False),
)


def _grouped(nav: dict[str, tuple[str, str]]) -> list[tuple[str, list[str], bool]]:
    """Groups filtered to the keys that actually exist, plus a catch-all.

    A page added to `nav` but forgotten here would otherwise become invisible
    and unreachable — a navigation bug no test would catch, because the page
    itself still works. Anything ungrouped lands in "Прочее" instead."""
    seen: set[str] = set()
    groups: list[tuple[str, list[str], bool]] = []
    for title, keys, expanded in NAV_GROUPS:
        present = [key for key in keys if key in nav]
        seen.update(present)
        if present:
            groups.append((title, present, expanded))
    leftover = [key for key in nav if key not in seen]
    if leftover:
        groups.append(("Прочее", leftover, False))
    return groups


def _select_page(key: str) -> None:
    st.session_state["nav_page"] = key
    st.query_params["page"] = key


def render_sidebar(
    nav: dict[str, tuple[str, str]],
    *,
    project_count: int,
    on_open_palette: Callable[[], None],
) -> str:
    """Render the primary navigation and return the active page key."""
    # Seeded before any widget exists: Streamlit refuses to reassign a widget's
    # key afterwards, and session state does not survive a browser refresh —
    # the URL does, so that is where "which section am I in" belongs.
    if "nav_page" not in st.session_state:
        requested = st.query_params.get("page")
        if requested in nav:
            st.session_state["nav_page"] = requested
    page_key = st.session_state.get("nav_page") or next(iter(nav))

    with st.sidebar:
        st.button(
            "Поиск и команды",
            icon=":material/search:",
            shortcut="Mod+K",
            on_click=on_open_palette,
            width="stretch",
            key="open_palette_btn",
        )

        for title, keys, expanded in _grouped(nav):
            # The group holding the current page is always open, whatever its
            # default: collapsing the section you are looking at hides where
            # you are.
            with st.expander(title, expanded=expanded or page_key in keys):
                for key in keys:
                    label, icon = nav[key]
                    st.button(
                        label,
                        icon=icon,
                        key=f"nav_btn_{key}",
                        width="stretch",
                        type="primary" if key == page_key else "tertiary",
                        on_click=_select_page,
                        args=(key,),
                    )

        st.divider()
        st.caption(f"Проектов в реестре: {project_count}")
        st.caption("Локальный режим · без внешних сервисов")

    if st.query_params.get("page") != page_key:
        st.query_params["page"] = page_key
    return page_key
