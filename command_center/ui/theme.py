"""Theme engine helper (UX-1 + UX-2a).

The actual theme (colors/radius/borders, light + dark) lives natively in
`.streamlit/config.toml`, per the project's Streamlit skill guidance. This
module is the one place allowed to inject global CSS (the UX-1 boundary) and
to read the *active* theme back at runtime, so no page reaches into
`st.context.theme` directly and duplicates this lookup.
"""

from __future__ import annotations

import streamlit as st


def current_theme_type() -> str:
    """Return the active theme as seen by the browser: "light" or "dark"."""
    return st.context.theme.type


def inject_global_css() -> None:
    """Inject the app-wide CSS layer (UX-2a).

    Bordered cards transition on hover. A 120 ms border/box-shadow
       transition makes interactive cards feel responsive instead of static,
       without adding ambient motion to idle elements (world-class apps stay
       still until you touch them).

    Polling fragments deliberately receive no opacity animation. Even a small
    fade made the whole interface appear to blink whenever the lightweight
    status strip refreshed.

    Emitted on every run: Streamlit drops any ``st.markdown`` a rerun does not
    re-emit, so a once-per-session guard would leave the page unstyled after
    the first interaction (audit H6 on ``home_dashboard.inject_css``). Called
    once from ``shell.render_shell`` after ``st.set_page_config``.
    """
    st.markdown(
        """
<style>
[data-testid="stVerticalBlockBorderWrapper"] {
  transition: border-color 140ms ease, box-shadow 140ms ease;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover {
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
</style>
""",
        unsafe_allow_html=True,
    )
