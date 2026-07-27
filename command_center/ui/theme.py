"""Theme engine helper (UX-1).

The actual theme (colors/radius/borders, light + dark) lives natively in
`.streamlit/config.toml`, per the project's Streamlit skill guidance — no
custom CSS. This module is the one place that reads the *active* theme back
at runtime, for the rare case a component needs to branch on it (e.g. a
future logo swap); nothing calls it yet, but it exists so no page reaches
into `st.context.theme` directly and duplicates this lookup.
"""

from __future__ import annotations

import streamlit as st


def current_theme_type() -> str:
    """Return the active theme as seen by the browser: "light" or "dark"."""
    return st.context.theme.type
