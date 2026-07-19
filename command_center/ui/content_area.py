"""Content area helpers (UX-1).

`page_header` centralizes the title(+caption) pattern that begins a page's
content area. Applied here to Workspace Home, the one page that already
paired a subheader with a caption inline; other pages keep their existing
single-line `st.subheader(...)` unchanged in this phase — no visual or
functional change, per UX-1's "do not redesign" scope.
"""

from __future__ import annotations

import streamlit as st


def page_header(title: str, caption: str | None = None) -> None:
    st.subheader(title)
    if caption:
        st.caption(caption)
