"""UX-2c: contextual Inspector + top command bar.

Guards the selection contract (``inspector_task_id`` / ``inspector_run_id``),
the popover label reflection, the live status glyph, and the inspect-on-card
buttons that load a task into the pane.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center.ui import inspector

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_task(task_id: str = "UXC-TEST-001", status: str = "Backlog") -> str:
    """Write a single minimal task into the isolated data dir so a Kanban card
    (with its inspect button) renders under pytest."""
    data_dir = Path(os.environ["AICC_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tasks.json").write_text(
        json.dumps([
            {
                "id": task_id,
                "title": "UX-2c inspector guard",
                "goal": "Smoke task for the inspector test.",
                "status": status,
                "project": "AI Command Center",
                "task_type": "feature",
                "priority": "Medium",
                "workflow_stage": "Draft",
                "branch": "feat/ux-2c",
                "depends_on": [],
                "parallel_group": "",
                "timeline": [],
                "launch_history": [],
                "dependencies": [],
            }
        ]),
        encoding="utf-8",
    )
    return task_id


def _at_on_kanban() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["nav_page"] = "kanban"
    at.run(timeout=60)
    return at


def test_inspector_keys_are_stable_contract():
    assert inspector.INSPECTOR_TASK_KEY == "inspector_task_id"
    assert inspector.INSPECTOR_RUN_KEY == "inspector_run_id"


def test_kanban_renders_inspect_button_per_card():
    _seed_task()
    at = _at_on_kanban()
    assert not at.exception, at.exception
    inspect_btns = [b for b in at.button if "_inspect" in (b.key or "")]
    assert inspect_btns, "no inspect button on the Kanban card"


def test_selecting_task_via_button_updates_inspector_selection():
    task_id = _seed_task()
    at = _at_on_kanban()
    assert not at.exception, at.exception
    inspect_btn = next((b for b in at.button if "_inspect" in (b.key or "")), None)
    assert inspect_btn is not None, "no inspect button to click"
    at = inspect_btn.click().run(timeout=60)
    assert not at.exception, at.exception
    assert inspector.INSPECTOR_TASK_KEY in at.session_state, "inspector_task_id not set after click"
    assert at.session_state[inspector.INSPECTOR_TASK_KEY] == task_id, (
        "inspector_task_id should be set to the clicked task"
    )
    assert inspector.INSPECTOR_RUN_KEY not in at.session_state, (
        "selecting a task must clear any prior run selection"
    )


def test_top_bar_live_status_glyph_aggregates_counts():
    """The glyph is computed from the non-superseded run snapshot (audit D5);
    stub `list_runs` to return the raw run rows."""
    from command_center.ui import top_bar

    class _FakeAPI:
        def list_runs(self, *, limit=None):
            return [
                {"id": "r1", "task_id": "A", "started_at": "1", "state": "RUNNING"},
                {"id": "r2", "task_id": "B", "started_at": "1", "state": "RUNNING"},
                {"id": "r3", "task_id": "C", "started_at": "1", "state": "RUNNING"},
                {"id": "r4", "task_id": "D", "started_at": "1", "state": "QUEUED"},
                {"id": "r5", "task_id": "E", "started_at": "1", "state": "FAILED"},
                {"id": "r6", "task_id": "F", "started_at": "1", "state": "FAILED"},
            ]

    glyph = top_bar._live_status_glyph(_FakeAPI())
    assert "⏺ 3" in glyph
    assert "⏸ 1" in glyph
    assert "⚠ 2" in glyph


def test_top_bar_live_status_glyph_ignores_superseded_failures():
    """A failed run a task has since retried must not keep counting as attention
    in the header — it agrees with the strip/supervisor (audit D5)."""
    from command_center.ui import top_bar

    class _FakeAPI:
        def list_runs(self, *, limit=None):
            return [
                {"id": "old", "task_id": "T", "started_at": "1", "state": "FAILED"},
                {"id": "new", "task_id": "T", "started_at": "2", "state": "RUNNING"},
            ]

    glyph = top_bar._live_status_glyph(_FakeAPI())
    assert "⚠" not in glyph
    assert "⏺ 1" in glyph


def test_top_bar_glyph_empty_when_no_api():
    from command_center.ui import top_bar
    assert top_bar._live_status_glyph(None) == ""


def test_run_detail_renders_real_run_columns():
    """_render_run_detail must surface the actual `run` columns — `provider_id`
    and `expected_branch` — not the nonexistent `executor`/`branch` keys, which
    would silently render a permanent em-dash for every run."""

    def _script():
        from command_center.ui import inspector as _insp

        _insp._render_run_detail(
            None,
            {
                "id": "run-abcdef123456",
                "project": "AIOS",
                "task_type": "implementation",
                "provider_id": "claude_code",
                "expected_branch": "feat/ux-2c",
                "state": "FAILED",
                "started_at": "2026-01-01T00:00:00",
                "completed_at": "2026-01-01T00:02:00",
                "exit_code": 1,
                "failure_reason": "session_expired",
                "task_id": "AIOS-1",
            },
        )

    at = AppTest.from_function(_script, default_timeout=30).run()
    assert not at.exception, at.exception
    captions = " | ".join(c.value for c in at.caption)
    assert "claude_code" in captions, f"provider_id not rendered (got: {captions})"
    assert "feat/ux-2c" in captions, f"expected_branch not rendered (got: {captions})"
