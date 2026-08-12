"""Design tokens for the Streamlit UI (UX-1).

Single source for spacing and semantic status-color values that previously
lived as separate, independently-maintained dicts in `app.py`. Streamlit has
no CSS-variable equivalent, so tokens here are plain Python constants
referenced by call sites instead of literals — colors map to Streamlit's
built-in badge/metric color names (`st.badge(..., color=...)`), and spacing
maps to the named sizes accepted by `gap=`/`st.space(...)`.

Values must stay behaviorally identical to what they replace; this module
only removes duplication, it does not restyle anything (`docs/desktop/
DESIGN_SYSTEM.md` §1 is the reference vocabulary for a future increment,
not a source of new values here).
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Spacing
# --------------------------------------------------------------------------

SPACE_SM = "small"
SPACE_MD = "medium"
SPACE_LG = "large"

# --------------------------------------------------------------------------
# Semantic status tones
# --------------------------------------------------------------------------
# Maps to Streamlit's built-in color names for st.badge/st.metric. Every
# per-domain color dict below (priority, launch status) derives from these
# same tones instead of picking colors independently, so a run's "failed"
# and a task's "Critical" priority never drift into visually distinct reds.

TONE_NEUTRAL = "gray"
TONE_INFO = "blue"
TONE_ACTIVE = "blue"
TONE_SUCCESS = "green"
TONE_WARNING = "orange"
TONE_DANGER = "red"
TONE_CANCELLED = "gray"

# Task priority (moved from app.py's local PRIORITY_COLORS — same values).
PRIORITY_COLORS: dict[str, str] = {
    "Low": TONE_NEUTRAL,
    "Medium": TONE_INFO,
    "High": TONE_WARNING,
    "Critical": TONE_DANGER,
}

# Kanban launch-confirmation status (moved from app.py's local
# LAUNCH_STATUS_COLORS — same values).
LAUNCH_STATUS_COLORS: dict[str, str] = {
    "Ready": TONE_NEUTRAL,
    "Launching": TONE_INFO,
    "Running": TONE_ACTIVE,
    "Completed": TONE_SUCCESS,
    "Failed": TONE_DANGER,
    "Cancelled": TONE_CANCELLED,
    "Requires Attention": TONE_WARNING,
}
