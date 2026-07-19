"""Shared rendering for a skipped/blocked batch-launch attempt (`execution_
queue.LaunchAttemptResult` with `launched=False`) — used by both the
Execution Queue panel and the Recommended Tasks panel, the two places that
call `execution_queue.launch_ready` from a plain button click, so a task's
exact `launch.validate_launch` warnings show up the same way in either
place instead of the old one-line generic summary."""

from __future__ import annotations

import streamlit as st


def render_skipped_launch(
    prefix: str,
    *,
    message: str,
    warnings: list[str],
    validation_report: dict | None,
    details_label: str | None = None,
) -> None:
    """`prefix` is the panel's own lead-in text (kept verbatim so each
    panel's wording stays what it already was, e.g. "Не запущено — {id}" or
    "Не удалось запустить сразу").

    When `warnings` is non-empty (the "blocked by validation warnings" case
    — dirty tree, detached HEAD, branch mismatch), renders every warning as
    its own bullet, plus a "Details" expander with the complete
    `validate_launch` report, instead of collapsing them into one generic
    sentence. Falls back to plain `message` for every other skip reason
    (missing workspace, task not found, launch exception) — those already
    carry their own specific text and have no warnings list to itemize.
    """
    if warnings:
        bullets = "\n".join(f"- {warning}" for warning in warnings)
        st.warning(f"{prefix}:\n{bullets}")
        if validation_report:
            suffix = f" — {details_label}" if details_label else ""
            with st.expander(f"Детали проверки{suffix}"):
                st.json(validation_report)
    else:
        st.warning(f"{prefix}: {message}")
