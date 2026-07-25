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

# (title, page keys, open by default). The UX analysis (task 1/2) found 17-20
# flat entries far past the 7±2 scanning limit, with three overview pages, two
# run monitors and several duplicate library pages competing for attention.
#
# Until the full page consolidation lands (design: 17 → 6 destinations), this
# regroups the same pages so the SIX core destinations sit in one open
# "Основное" group an operator can scan at a glance, and everything else —
# secondary planning surfaces, analytics, portfolio, and the pages the redesign
# folds away — collapses out of the daily path. No page is removed (the command
# palette and every deep link still reach all of them); they are just no longer
# all shouting at once.
NAV_GROUPS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # The core destinations the redesign keeps: overview, planning, execution,
    # projects, git. Chat/reports/generated/context are no longer their own
    # sidebar entries — they live inside the project view (task 02661825).
    ("Основное", ("dashboard", "kanban", "execution_center", "projects", "git_center"), True),
    ("Планирование", ("waves", "create"), False),
    ("Аналитика", ("executive", "runs", "timeline", "agents"), False),
    ("Портфель и режимы", ("portfolio", "portfolio_overview", "workspace_home", "workspace", "focus"), False),
)

# Pages whose handler is kept (for delegation / an existing deep link) but which
# are no longer their own sidebar entry — everything about a project now opens
# inside the project view as a tab: chat, generated tasks, reports, context.
HIDDEN_FROM_SIDEBAR: frozenset[str] = frozenset({"chat", "generated", "reports", "context"})


def _grouped(nav: dict[str, tuple[str, str]]) -> list[tuple[str, list[str], bool]]:
    """Groups filtered to the keys that actually exist, plus a catch-all.

    A page added to `nav` but forgotten here would otherwise become invisible
    and unreachable — a navigation bug no test would catch, because the page
    itself still works. Anything ungrouped lands in "Прочее" instead."""
    seen: set[str] = set()
    groups: list[tuple[str, list[str], bool]] = []
    for title, keys, expanded in NAV_GROUPS:
        present = [key for key in keys if key in nav and key not in HIDDEN_FROM_SIDEBAR]
        seen.update(present)
        if present:
            groups.append((title, present, expanded))
    # `HIDDEN_FROM_SIDEBAR` pages are intentionally omitted from the catch-all
    # too: their handler stays reachable by URL / delegation, but they are not
    # a sidebar destination (they live inside the project view).
    leftover = [key for key in nav if key not in seen and key not in HIDDEN_FROM_SIDEBAR]
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
