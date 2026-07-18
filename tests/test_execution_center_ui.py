"""Streamlit AppTest coverage for the Live Execution Center v2 dashboard —
a thin UI consumer of the frozen v2 runtime (`command_center.runtime`) plus
the new projection/reconciliation/task-sync modules under
`command_center/runtime/`. Every test here drives `app.py` through
`streamlit.testing.v1.AppTest`, the same harness `tests/test_app_streamlit.py`
uses for the rest of the app, plus the v2 runtime's own `fake_claude`/
`configure_project_repo`/`git_repo` fixtures (`tests/conftest.py`) so no test
launches the real Claude Code CLI or spends API credits.

`st.cache_resource` (used by `app.py`'s `get_execution_center_api()`
singleton) caches process-wide, not per-`AppTest`-instance — `conftest.py`'s
`isolated_data_dir` (autouse) clears that cache every time it resets
`AICC_DATA_DIR`, so each test here still gets a `Supervisor` constructed
fresh against its own isolated data dir without needing its own fixture.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db
from command_center.runtime import identity
from command_center.runtime import session_view

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _at_on_page(page_key: str, **extra_session_state) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = page_key
    for key, value in extra_session_state.items():
        at.session_state[key] = value
    at.run()
    return at


def _launch_via_ui(
    at: AppTest, *, project: str = "AIOS", task_type: str = "review", instruction: str = "do the thing"
) -> AppTest:
    """Fill in and submit the ad-hoc launch form exactly as a user would, so
    the run ends up owned by *this* `AppTest` session's cached Supervisor
    instance (required for cancellation — see `Supervisor.cancel`'s
    docstring)."""
    at.selectbox(key="exec_center_launch_project").select(project).run()
    at.selectbox(key="exec_center_launch_task_type").select(task_type).run()
    at.text_area(key="exec_center_launch_instruction").set_value(instruction).run()
    at.checkbox(key="exec_center_launch_confirm").check().run()
    at = at.button(key="exec_center_launch_btn").click().run()
    return at.run()


def _most_recent_run_id(db_path) -> str:
    runs = runtime_db.list_runs(db_path, limit=1)
    assert runs, "expected at least one run row in runtime.db"
    return runs[0]["id"]


def _wait_for_report(db_path, run_id: str, *, timeout: float = 10.0) -> None:
    """Block until `Supervisor._supervise`'s background thread has fully
    finished a run — not just until `run.state` turns terminal (set partway
    through that same thread, *before* report-saving), but until its report
    row exists (the last DB write that thread makes, right before it exits).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runtime_db.get_report(db_path, run_id) is not None:
            return
        time.sleep(0.05)
    raise AssertionError(f"run {run_id!r} did not finish in the background within {timeout}s")


# --------------------------------------------------------------------------
# 1. Page renders and navigation entry exists
# --------------------------------------------------------------------------


def test_execution_center_page_renders_and_nav_entry_exists():
    at = _at_on_page("execution_center")
    assert not at.exception
    assert at.subheader[0].value == "Live Execution Center"

    nav_options = at.radio(key="nav_page").options
    assert any("Live Execution Center" in option for option in nav_options)


def _assert_execution_center_page_rendered(at: AppTest) -> None:
    assert not at.exception
    assert at.subheader[0].value == "Live Execution Center"
    assert any("Запустить новый прогон" in status.label for status in at.status)


def test_api_singleton_not_recreated_across_reruns(monkeypatch):
    construct_calls = []
    original_init = runtime_api.ExecutionCenterAPI.__init__

    def counting_init(self, *args, **kwargs):
        construct_calls.append(1)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(runtime_api.ExecutionCenterAPI, "__init__", counting_init)

    at = _at_on_page("execution_center")
    _assert_execution_center_page_rendered(at)
    assert len(construct_calls) == 1

    for _ in range(3):
        at = at.run()
        _assert_execution_center_page_rendered(at)
        assert len(construct_calls) == 1, "ExecutionCenterAPI must not be reconstructed across reruns"


# --------------------------------------------------------------------------
# 2. Launch controls call the public runtime API with expected validated inputs
# --------------------------------------------------------------------------


def test_launch_calls_start_run_with_expected_inputs(monkeypatch, git_repo, configure_project_repo):
    configure_project_repo("AIOS", git_repo)
    captured: dict = {}

    def fake_start_run(self, **kwargs):
        captured.update(kwargs)
        return {"id": "fake-run-id", "state": "RUNNING", "session_id": "s1"}

    monkeypatch.setattr(runtime_api.ExecutionCenterAPI, "start_run", fake_start_run)

    at = _at_on_page("execution_center")
    at.selectbox(key="exec_center_launch_project").select("AIOS").run()
    at.selectbox(key="exec_center_launch_task_type").select("review").run()
    at.text_area(key="exec_center_launch_instruction").set_value("do the thing").run()
    at.checkbox(key="exec_center_launch_confirm").check().run()
    at = at.button(key="exec_center_launch_btn").click().run()

    assert not at.exception
    assert captured["project"] == "AIOS"
    assert captured["task_type"] == "review"
    assert captured["instruction"] == "do the thing"
    assert captured["confirmed"] is True
    assert captured["repository_path"] == str(git_repo)
    assert captured["timeout_seconds"] == runtime_api.DEFAULT_TIMEOUT_SECONDS


# --------------------------------------------------------------------------
# 3 & 4. Sensitive (BANK/LEGAL) confirmation gate
# --------------------------------------------------------------------------


def test_sensitive_launch_blocked_without_extra_confirmation(monkeypatch, git_repo, configure_project_repo):
    configure_project_repo("BANK", git_repo)
    calls: list[dict] = []
    monkeypatch.setattr(
        runtime_api.ExecutionCenterAPI,
        "start_run",
        lambda self, **kwargs: (calls.append(kwargs), {"id": "x", "state": "RUNNING", "session_id": "s"})[1],
    )

    at = _at_on_page("execution_center")
    at.selectbox(key="exec_center_launch_project").select("BANK").run()
    at.selectbox(key="exec_center_launch_task_type").select("review").run()
    at.text_area(key="exec_center_launch_instruction").set_value("sensitive task").run()
    at.checkbox(key="exec_center_launch_confirm").check().run()
    at = at.button(key="exec_center_launch_btn").click().run()

    assert not calls, "start_run must not be called for a sensitive project without the extra confirmation"
    assert any("чувствительный" in w.value for w in at.warning)
    assert any("заблокирован" in e.value for e in at.error)


def test_sensitive_launch_accepted_with_confirmation(monkeypatch, git_repo, configure_project_repo):
    configure_project_repo("BANK", git_repo)
    calls: list[dict] = []

    def fake_start_run(self, **kwargs):
        calls.append(kwargs)
        return {"id": "x", "state": "RUNNING", "session_id": "s"}

    monkeypatch.setattr(runtime_api.ExecutionCenterAPI, "start_run", fake_start_run)

    at = _at_on_page("execution_center")
    at.selectbox(key="exec_center_launch_project").select("BANK").run()
    at.selectbox(key="exec_center_launch_task_type").select("review").run()
    at.text_area(key="exec_center_launch_instruction").set_value("sensitive task").run()
    at.checkbox(key="exec_center_launch_confirm").check().run()
    at.checkbox(key="exec_center_launch_sensitivity_ack").check().run()
    at = at.button(key="exec_center_launch_btn").click().run()

    assert len(calls) == 1
    assert calls[0]["project"] == "BANK"
    assert calls[0]["confirmed"] is True


# --------------------------------------------------------------------------
# 5. Non-blocking launch; Running status is shown as a card
# --------------------------------------------------------------------------


def test_launch_is_nonblocking_and_running_status_is_displayed(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)

    at = _at_on_page("execution_center")
    at.selectbox(key="exec_center_launch_project").select("AIOS").run()
    at.selectbox(key="exec_center_launch_task_type").select("review").run()
    at.text_area(key="exec_center_launch_instruction").set_value("do the thing").run()
    at.checkbox(key="exec_center_launch_confirm").check().run()

    started = time.monotonic()
    at = at.button(key="exec_center_launch_btn").click().run()
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"launch appears to have blocked for {elapsed:.2f}s (fake_claude sleeps 5s before exiting)"

    at = at.run()
    assert not at.exception
    assert any(f"Статус: **{session_view.STATUS_RUNNING}**" in c.value for c in at.caption)

    run_id = _most_recent_run_id(runtime_db.resolve_db_path())
    at.checkbox(key=f"exec_card_cancel_ack_{run_id}").check().run()
    at.button(key=f"exec_card_cancel_btn_{run_id}").click().run()
    _wait_for_report(runtime_db.resolve_db_path(), run_id)


# --------------------------------------------------------------------------
# 6 & 7. Cancellation requires confirmation, calls the public API, and is
# only ever offered while the run is actually Running.
# --------------------------------------------------------------------------


def test_cancel_requires_confirmation_before_calling_api(monkeypatch, git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)

    cancel_calls: list[tuple] = []
    original_cancel = runtime_api.ExecutionCenterAPI.request_cancel

    def spy_cancel(self, run_id, **kwargs):
        cancel_calls.append((run_id, kwargs))
        return original_cancel(self, run_id, **kwargs)

    monkeypatch.setattr(runtime_api.ExecutionCenterAPI, "request_cancel", spy_cancel)

    at = _at_on_page("execution_center")
    at = _launch_via_ui(at)
    run_id = _most_recent_run_id(runtime_db.resolve_db_path())
    cancel_btn_key = f"exec_card_cancel_btn_{run_id}"

    at = at.button(key=cancel_btn_key).click().run()
    assert not cancel_calls, "request_cancel must not be called before the cancel checkbox is confirmed"

    at.checkbox(key=f"exec_card_cancel_ack_{run_id}").check().run()
    at = at.button(key=cancel_btn_key).click().run()
    assert cancel_calls == [(run_id, {"confirmed": True})]

    _wait_for_report(runtime_db.resolve_db_path(), run_id)


@pytest.mark.parametrize("state", ["PREPARED", "QUEUED"])
def test_cancel_action_hidden_for_non_running_states(state):
    api = runtime_api.ExecutionCenterAPI()
    task = runtime_db.create_task(api.db_path, project="AIOS", title="t", task_type="review")
    session = runtime_db.create_session(api.db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = runtime_db.create_run(
        api.db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="review",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )
    if state == "QUEUED":
        run = runtime_db.update_run_state(api.db_path, run["id"], expected_version=run["version"], new_state="QUEUED")

    at = _at_on_page("execution_center")
    assert not at.exception
    assert not any(cb.key == f"exec_card_cancel_ack_{run['id']}" for cb in at.checkbox)
    assert not any(b.key == f"exec_card_cancel_btn_{run['id']}" for b in at.button)


def test_cancel_action_visible_while_running(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)

    at = _at_on_page("execution_center")
    at = _launch_via_ui(at)
    run_id = _most_recent_run_id(runtime_db.resolve_db_path())

    assert any(cb.key == f"exec_card_cancel_ack_{run_id}" for cb in at.checkbox)
    assert any(b.key == f"exec_card_cancel_btn_{run_id}" for b in at.button)

    at.checkbox(key=f"exec_card_cancel_ack_{run_id}").check().run()
    at.button(key=f"exec_card_cancel_btn_{run_id}").click().run()
    _wait_for_report(runtime_db.resolve_db_path(), run_id)


# --------------------------------------------------------------------------
# 8. UI eventually reflects Cancelled after runtime persistence changes
# --------------------------------------------------------------------------


def test_cancelled_status_eventually_displayed(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)

    at = _at_on_page("execution_center")
    at = _launch_via_ui(at)
    run_id = _most_recent_run_id(runtime_db.resolve_db_path())

    at.checkbox(key=f"exec_card_cancel_ack_{run_id}").check().run()
    at = at.button(key=f"exec_card_cancel_btn_{run_id}").click().run()
    assert any("отправлен" in s.value for s in at.success) or any(
        f"Статус: **{session_view.STATUS_CANCELLED}**" in c.value for c in at.caption
    )

    deadline = time.monotonic() + 10
    displayed_cancelled = False
    while time.monotonic() < deadline:
        at = at.run()
        if any(f"Статус: **{session_view.STATUS_CANCELLED}**" in c.value for c in at.caption):
            displayed_cancelled = True
            break
        time.sleep(0.2)

    assert displayed_cancelled
    _wait_for_report(runtime_db.resolve_db_path(), run_id)


# --------------------------------------------------------------------------
# 9. Terminal run status remains visible after page rerun / revisit
# --------------------------------------------------------------------------


def test_terminal_status_persists_across_page_revisit(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)

    api = runtime_api.ExecutionCenterAPI()
    run = api.start_run(
        project="AIOS", repository_path=str(git_repo), task_type="review", instruction="p", confirmed=True
    )
    final = api.supervisor.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"

    at = _at_on_page("dashboard")
    assert not at.exception

    at2 = _at_on_page("execution_center")
    assert not at2.exception
    assert any(f"Статус: **{session_view.STATUS_COMPLETED}**" in c.value for c in at2.caption)


# --------------------------------------------------------------------------
# 10. Failed status displays the last error
# --------------------------------------------------------------------------


def test_failed_status_displays_last_error(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    configure_project_repo("AIOS", git_repo)

    api = runtime_api.ExecutionCenterAPI()
    run = api.start_run(
        project="AIOS",
        repository_path=str(git_repo),
        task_type="review",
        instruction="p",
        confirmed=True,
        timeout_seconds=1,
    )
    final = api.supervisor.wait_for_run(run["id"], timeout=15)
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "timeout"

    at = _at_on_page("execution_center")
    assert not at.exception
    assert any(f"Статус: **{session_view.STATUS_FAILED}**" in c.value for c in at.caption)
    assert any("timeout" in e.value for e in at.error)


# --------------------------------------------------------------------------
# 11. Existing app navigation and existing pages remain functional
# --------------------------------------------------------------------------


def test_existing_pages_still_render_without_exception():
    for page_key in ("dashboard", "agents", "runs", "executive"):
        at = _at_on_page(page_key)
        assert not at.exception, f"page {page_key!r} raised: {at.exception}"


# --------------------------------------------------------------------------
# 12. Section bucketing: a hand-built run in each state renders under the
# expected dashboard section, reusing the reconciliation-test style of
# building `run` rows directly against `db` (tests/test_runtime_reconciliation.py).
# --------------------------------------------------------------------------


def _make_run_row(
    db_path, *, state: str, cancel_requested: bool = False, failure_reason: str | None = None, pid: int | None = None
) -> dict:
    """`pid=None` (the default) is correct for every terminal state — only a
    hand-built `RUNNING` row needs a *real, currently-alive* pid (see the
    two callers below that spawn a real throwaway process), otherwise
    `Supervisor.reconcile()` — which the dashboard now calls on every render,
    exactly as the mission requires — will honestly (and correctly)
    reclassify a "Running" row with no provable process behind it as
    `INTERRUPTED`, per its own conservative "never assume Running because a
    field says so" contract."""
    task = runtime_db.create_task(db_path, project="AIOS", title="Bucketing task", task_type="review")
    session = runtime_db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="review",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )
    if state != "PREPARED":
        run = runtime_db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    if state not in ("PREPARED", "QUEUED"):
        running_fields = {"started_at": "2026-01-01T00:00:00"}
        if pid is not None:
            running_fields["pid"] = pid
            recorded_identity = identity.capture_identity(pid)
            running_fields["process_start_identity"] = recorded_identity.as_string() if recorded_identity else None
        run = runtime_db.update_run_state(
            db_path, run["id"], expected_version=run["version"], new_state="RUNNING", fields=running_fields
        )
    if cancel_requested:
        run = runtime_db.update_run_fields(db_path, run["id"], expected_version=run["version"], fields={"cancel_requested": 1})
    if state not in ("PREPARED", "QUEUED", "RUNNING"):
        fields = {"completed_at": "2026-01-01T00:01:00"}
        if failure_reason:
            fields["failure_reason"] = failure_reason
        run = runtime_db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state=state, fields=fields)
    return run


@pytest.mark.parametrize(
    "state,cancel_requested,expected_caption",
    [
        ("RUNNING", False, session_view.STATUS_RUNNING),
        ("RUNNING", True, session_view.STATUS_WAITING),
        ("COMPLETED", False, session_view.STATUS_COMPLETED),
        ("FAILED", False, session_view.STATUS_FAILED),
        ("INTERRUPTED", False, session_view.STATUS_REQUIRES_ATTENTION),
        ("UNKNOWN", False, session_view.STATUS_REQUIRES_ATTENTION),
        ("CANCELLED", False, session_view.STATUS_CANCELLED),
    ],
)
def test_dashboard_renders_each_status_bucket(state, cancel_requested, expected_caption):
    api = runtime_api.ExecutionCenterAPI()
    proc = subprocess.Popen(["sleep", "5"]) if state == "RUNNING" else None
    try:
        _make_run_row(api.db_path, state=state, cancel_requested=cancel_requested, pid=proc.pid if proc else None)

        at = _at_on_page("execution_center")
        assert not at.exception
        assert any(f"Статус: **{expected_caption}**" in c.value for c in at.caption)
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait()


# --------------------------------------------------------------------------
# 13. Auto-refresh does not create duplicate runs or duplicate task mutations
# --------------------------------------------------------------------------


def test_auto_refresh_does_not_duplicate_runs_or_task_mutations(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)

    api = runtime_api.ExecutionCenterAPI()
    run = api.start_run(
        project="AIOS", repository_path=str(git_repo), task_type="review", instruction="p", confirmed=True
    )
    api.supervisor.wait_for_run(run["id"], timeout=10)
    _wait_for_report(api.db_path, run["id"])

    at = _at_on_page("execution_center")
    for _ in range(3):
        at = at.run()
        assert not at.exception

    all_runs = runtime_db.list_runs(api.db_path, limit=100)
    assert len(all_runs) == 1, "repeated refreshes must never create a new run"
