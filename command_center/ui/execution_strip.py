"""Execution Strip (UX-2a): a slim, always-visible status bar that surfaces
what is running right now regardless of which page is open.

It is a `@st.fragment(run_every=5s)`, so it polls the runtime database on its
own cadence and repaints *only itself* — the page behind it never blanks just
because the strip ticked. This is the direct answer to the operator's reported
"every few seconds the whole page visibly refreshes" pain: execution state
becomes one glance away on every page without a per-page auto-refresh.

The strip deliberately mirrors the Live Execution Center's bucket vocabulary
(``live_board``: live / waiting / attention) so an operator does not learn a
second word for the same thing here. Counts come from cheap raw-state filtering
of ``api.list_runs`` — the strip is a status glyph, not a second board, so it
does not build the full session views the Execution Center page does.
"""

from __future__ import annotations

import streamlit as st

from command_center.runtime.api import ExecutionCenterAPI
from command_center.ui import tokens

# Raw run states (``runtime/session_view.py``) grouped the way the board does.
_LIVE_STATES = frozenset({"RUNNING", "STARTING", "LAUNCHING"})
_WAITING_STATES = frozenset({"WAITING"})
_ATTENTION_STATES = frozenset({"FAILED", "BLOCKED", "INCOMPLETE"})


@st.fragment(run_every=5.0)
def render_execution_strip(api: ExecutionCenterAPI) -> None:
    """Render the cross-page execution status strip.

    Polls ``api.list_runs`` every 5 s and shows live / waiting / attention
    counts with a single button to jump to the Live Execution Center. When
    nothing is running and nothing needs attention the strip renders a quiet
    idle line so the bar never looks broken-empty.
    """
    runs = api.list_runs(limit=100)
    live = sum(1 for r in runs if r.get("state") in _LIVE_STATES)
    waiting = sum(1 for r in runs if r.get("state") in _WAITING_STATES)
    attention = sum(1 for r in runs if r.get("state") in _ATTENTION_STATES)

    with st.container(border=True, key="exec_strip"):
        left, right = st.columns([5, 2], vertical_alignment="center")
        with left:
            parts: list[str] = []
            if live:
                parts.append(f"▲ {live} выполняется")
            if waiting:
                parts.append(f"◷ {waiting} в очереди")
            if attention:
                parts.append(f"● {attention} требуют внимания")
            label = "  ·  ".join(parts) if parts else "Система простаивает — активных прогонов нет"
            tone = tokens.TONE_DANGER if attention else tokens.TONE_ACTIVE if live else tokens.TONE_NEUTRAL
            st.markdown(f"**{label}**")
            st.caption("Live Execution Center · автообновление каждые 5 с", )
            # The tone is exposed as a colored badge so a glance catches the
            # worst state (attention) without reading the label.
            st.badge(
                "внимание" if attention else "в работе" if live else "ожидание",
                color=tone,
            )
        with right:
            if st.button(
                "Execution Center",
                icon=":material/bolt:",
                key="exec_strip_open",
                width="stretch",
                type="primary" if (live or attention) else "tertiary",
            ):
                st.session_state.pending_nav = "execution_center"
                st.rerun()