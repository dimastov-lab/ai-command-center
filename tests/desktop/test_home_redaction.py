"""BANK/LEGAL dual-layer redaction regression for the native Home page (D2D, §10).

Mirrors the Streamlit page's `test_workspace_home_bank_only_state_never_renders_banned_values`:
a sensitive-project scenario is built through the *real* read model, then checked
at two independent layers — (1) the snapshot dict, and (2) the rendered widget
tree — so a banned raw value can never leak, even if a future change bypasses the
snapshot. Redaction itself lives in `command_center.workspace_home`; the desktop
renderer only presents already-sanitized data.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from command_center import activity_log, agent_runner, models, workspace_home
from command_center.runtime.api import ExecutionCenterAPI
from command_center.desktop.pages.home import HomePage

_BANNED_VALUES = ["TOP SECRET INSTRUCTION", "secret-financial-detail"]


@pytest.fixture
def _isolated_artifact_dirs(isolated_data_dir, monkeypatch):
    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_home, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(workspace_home, "GENERATED_DIR", generated_dir)
    return generated_dir, reports_dir


def _all_widget_text(root: QWidget) -> str:
    parts: list[str] = []
    for widget in [root, *root.findChildren(QWidget)]:
        for getter in ("text", "accessibleName", "accessibleDescription", "toolTip", "placeholderText"):
            fn = getattr(widget, getter, None)
            if callable(fn):
                try:
                    value = fn()
                except TypeError:
                    continue
                if isinstance(value, str):
                    parts.append(value)
    return "\n".join(parts)


def test_home_bank_only_state_never_renders_banned_values(qtbot, _isolated_artifact_dirs):
    generated_dir, reports_dir = _isolated_artifact_dirs
    (generated_dir / "BANK").mkdir(parents=True, exist_ok=True)
    (generated_dir / "BANK" / "abc123_implementation.md").write_text("secret-financial-detail")
    (reports_dir / "BANK").mkdir(parents=True, exist_ok=True)
    (reports_dir / "BANK" / "report.md").write_text("TOP SECRET INSTRUCTION report body")

    activity_log.log_event("run_completed", project="BANK", message="secret-financial-detail in message")

    run = models.new_run_record(
        project="BANK", task_id=None, agent="claude_code", task_type="implementation",
        repository_path="/tmp/x", prompt="TOP SECRET INSTRUCTION", timeout_seconds=60,
    )
    run["status"] = "completed"
    run["report_path"] = "reports/BANK/report.md"
    run["parsed"] = {"verdict": "APPROVED_FOR_COMMIT", "findings": {s: [] for s in models.SEVERITIES}}
    agent_runner.append_run(run)

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=ExecutionCenterAPI())

    # Layer 1 — the snapshot dict itself: BANK entries carry no banned value.
    bank_entries = (
        [r for r in snapshot["recent_runs"] if r.get("project") == "BANK"]
        + [r for r in snapshot["reports"] if r.get("project") == "BANK"]
        + [r for r in snapshot["artifacts"] if r.get("project") == "BANK"]
        + [r for r in snapshot["recent_activity"] if r.get("project") == "BANK"]
    )
    snapshot_text = repr(bank_entries)
    for banned in _BANNED_VALUES:
        assert banned not in snapshot_text

    # Layer 2 — the rendered widget tree: a second, independent line of defense.
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(snapshot)
    rendered = _all_widget_text(page)
    for banned in _BANNED_VALUES:
        assert banned not in rendered
    assert "BANK" in rendered  # the project is still shown — redacted, not hidden
