"""Inspector placeholder panel (UX-1).

A stub for a future contextual detail pane (e.g. the selected run or task).
Deliberately inert in this phase: no data, no interactions — wiring it to
real content is a later UX phase's work, not this one's.
"""

from __future__ import annotations

import streamlit as st

from command_center.ui import tokens


def render_placeholder() -> None:
    with st.container(border=True, gap=tokens.SPACE_SM):
        st.caption("Инспектор")
        st.info(
            "Контекстные детали появятся здесь в следующей фазе UX.",
            icon=":material/info:",
        )
