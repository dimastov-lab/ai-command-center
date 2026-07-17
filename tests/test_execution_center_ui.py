"""Streamlit AppTest coverage for the Live Execution Center page (Sprint 2
Increment 1) — a thin UI consumer of the frozen v2 runtime
(`command_center.runtime`). Every test here drives `app.py` through
`streamlit.testing.v1.AppTest`, the same harness `tests/test_app_streamlit.py`
uses for the rest of the app, plus the v2 runtime's own `fake_claude`/
`configure_project_repo`/`git_repo` fixtures (`tests/conftest.py`) so no test
launches the real Claude Code CLI or spends API credits.

`st.cache_resource` (used by `app.py`'s `get_execution_center_api()` singleton)
caches process-wide, not per-`AppTest`-instance — verified empirically: a
second, independent `AppTest.from_file(...)` in the same pytest process reuses
the first one's cached `ExecutionCenterAPI`/`Supervisor`. Combined with
`isolated_data_dir` (conftest, autouse) wiping `AICC_DATA_DIR` before every
test, a stale cached `Supervisor` would otherwise point at a `runtime.db` path
whose schema was never (re-)migrated for the fresh directory. `_fresh_execution_center_singleton`
below clears that cache before every test in this file so each test gets a
`Supervisor` constructed fresh against its own isolated data dir.
"""

from __future__ import annotations

import re
import time

import streamlit as st
from streamlit.testing.v1 import AppTest

import pytest
from pathlib import Path

from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _fresh_execution_center_singleton(isolated_data_dir):
    """Force `get_execution_center_api()` to construct a new `ExecutionCenterAPI`
    (and run `db.migrate()`) against *this* test's isolated data dir, instead
    of reusing a Supervisor cached from a previous test's (now-deleted) one."""
    st.cache_resource.clear()
    yield


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
    """Fill in and submit the launch form exactly as a user would, so the run
    ends up owned by *this* `AppTest` session's cached Supervisor instance
    (required for cancellation — see `Supervisor.cancel`'s docstring on why a
    run can only be cancelled by the same process instance that launched it)."""
    at.selectbox(key="exec_center_launch_project").select(project).run()
    at.selectbox(key="exec_center_launch_task_type").select(task_type).run()
    at.text_area(key="exec_center_launch_instruction").set_value(instruction).run()
    at.checkbox(key="exec_center_launch_confirm").check().run()
    at = at.button(key="exec_center_launch_btn").click().run()
    return at.run()  # settle pending_exec_center_run -> exec_center_run_selector


def _current_run_id(at: AppTest) -> str:
    return at.session_state["exec_center_run_selector"]


def _wait_for_report(db_path, run_id: str, *, timeout: float = 10.0) -> None:
    """Block until `Supervisor._supervise`'s background thread has fully
    finished a run — not just until `run.state` turns terminal (set partway
    through that same thread, *before* report-saving), but until its report
    row exists (the last DB write that thread makes, right before it exits).

    A test that returns while that thread is still running would race the
    next test's `isolated_data_dir` fixture wiping `AICC_DATA_DIR` out from
    under it — this closes that race without needing a handle on whichever
    `Supervisor` instance (the UI's cached singleton, or a test-local one)
    happens to own the run."""
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


# --------------------------------------------------------------------------
# 2. API singleton is not recreated on every Streamlit rerun
# --------------------------------------------------------------------------


def test_api_singleton_not_recreated_across_reruns(monkeypatch):
    construct_calls = []
    original_init = runtime_api.ExecutionCenterAPI.__init__

    def counting_init(self, *args, **kwargs):
        construct_calls.append(1)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(runtime_api.ExecutionCenterAPI, "__init__", counting_init)

    at = _at_on_page("execution_center")
    at = at.run()
    at = at.run()

    assert len(construct_calls) == 1


# --------------------------------------------------------------------------
# 3. Launch controls call the public runtime API with expected validated inputs
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
# 4 & 5. Sensitive (BANK/LEGAL) confirmation gate
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
    # Deliberately leave exec_center_launch_sensitivity_ack unchecked.
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
# 6 & 7. Non-blocking launch; RUNNING state displayed
# --------------------------------------------------------------------------


def test_launch_is_nonblocking_and_running_state_is_displayed(git_repo, configure_project_repo, fake_claude):
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
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Статус") == "Выполняется"

    # Terminate the still-sleeping fake_claude process promptly instead of
    # leaving it (and this run's Supervisor background threads) alive past
    # the end of the test — see `_wait_for_report`.
    run_id = _current_run_id(at)
    at.checkbox(key=f"exec_center_cancel_confirm_{run_id}").check().run()
    at.button(key=f"exec_center_cancel_btn_{run_id}").click().run()
    _wait_for_report(runtime_db.resolve_db_path(), run_id)


# --------------------------------------------------------------------------
# 8. Incremental events appear without duplicate rendering
# --------------------------------------------------------------------------


def test_incremental_events_appear_without_duplicates(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_DELAY"] = "0.1"
    configure_project_repo("AIOS", git_repo)

    api = runtime_api.ExecutionCenterAPI()
    run = api.start_run(
        project="AIOS", repository_path=str(git_repo), task_type="review", instruction="p", confirmed=True
    )

    at = _at_on_page("execution_center", exec_center_run_selector=run["id"])
    seen_counts: list[int] = []
    deadline = time.monotonic() + 10
    final_state = None
    while time.monotonic() < deadline:
        log_blocks = [c.value for c in at.code if c.value.startswith("[")]
        if log_blocks:
            seqs = [int(n) for n in re.findall(r"\[\s*(\d+)\]", log_blocks[0])]
            assert len(seqs) == len(set(seqs)), f"duplicate seq numbers rendered: {seqs}"
            assert seqs == sorted(seqs)
            seen_counts.append(len(seqs))
        current = api.get_run(run["id"])
        final_state = current["state"]
        if final_state in runtime_db.TERMINAL_STATES:
            break
        time.sleep(0.15)
        at = at.run()

    assert final_state in runtime_db.TERMINAL_STATES, "run never reached a terminal state within the test deadline"
    assert seen_counts, "expected at least one poll to show rendered log lines"
    assert seen_counts == sorted(seen_counts), "accumulated event count must never shrink or reorder across polls"

    _wait_for_report(api.db_path, run["id"])


# --------------------------------------------------------------------------
# 9 & 10. Cancellation requires confirmation and calls the public API
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
    run_id = _current_run_id(at)
    cancel_btn_key = f"exec_center_cancel_btn_{run_id}"

    at = at.button(key=cancel_btn_key).click().run()
    assert not cancel_calls, "request_cancel must not be called before the cancel checkbox is confirmed"
    assert any("заблокирован" in e.value for e in at.error)

    at.checkbox(key=f"exec_center_cancel_confirm_{run_id}").check().run()
    at = at.button(key=cancel_btn_key).click().run()
    assert cancel_calls == [(run_id, {"confirmed": True})]

    _wait_for_report(runtime_db.resolve_db_path(), run_id)


# --------------------------------------------------------------------------
# 11. UI eventually displays CANCELLED after runtime persistence changes
# --------------------------------------------------------------------------


def test_cancelled_state_eventually_displayed(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)

    at = _at_on_page("execution_center")
    at = _launch_via_ui(at)
    run_id = _current_run_id(at)

    at.checkbox(key=f"exec_center_cancel_confirm_{run_id}").check().run()
    at = at.button(key=f"exec_center_cancel_btn_{run_id}").click().run()
    assert any("отправлен" in s.value for s in at.success)

    state = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        at = at.run()
        metrics = {m.label: m.value for m in at.metric}
        state = metrics.get("Статус")
        if state == "Отменено":
            break
        time.sleep(0.2)

    assert state == "Отменено"
    _wait_for_report(runtime_db.resolve_db_path(), run_id)


# --------------------------------------------------------------------------
# 12. Terminal run state remains visible after page rerun / revisit
# --------------------------------------------------------------------------


def test_terminal_state_persists_across_page_revisit(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)

    api = runtime_api.ExecutionCenterAPI()
    run = api.start_run(
        project="AIOS", repository_path=str(git_repo), task_type="review", instruction="p", confirmed=True
    )
    final = api.supervisor.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"

    # Simulate leaving the page (a rerun that lands elsewhere)...
    at = _at_on_page("dashboard")
    assert not at.exception

    # ...and a fresh page load/revisit (a brand-new AppTest session, so no
    # `st.session_state` continuity — the DB-backed recent-runs selector is
    # the only thing that can recover the run, per the increment's design).
    at2 = _at_on_page("execution_center")
    assert not at2.exception
    metrics = {m.label: m.value for m in at2.metric}
    assert metrics.get("Статус") == "Завершено"


# --------------------------------------------------------------------------
# 13. FAILED state displays failure reason
# --------------------------------------------------------------------------


def test_failed_state_displays_failure_reason(git_repo, configure_project_repo, fake_claude):
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

    at = _at_on_page("execution_center", exec_center_run_selector=run["id"])
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Статус") == "Ошибка"
    assert any("timeout" in e.value for e in at.error)


# --------------------------------------------------------------------------
# 14. Existing app navigation and existing pages remain functional
# --------------------------------------------------------------------------


def test_existing_pages_still_render_without_exception():
    for page_key in ("dashboard", "agents", "runs", "executive"):
        at = _at_on_page(page_key)
        assert not at.exception, f"page {page_key!r} raised: {at.exception}"
