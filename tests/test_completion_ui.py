"""AppTest coverage for the Execution Center completion panel (spec UI cases
21-24). Drives `app.py` through Streamlit's AppTest and asserts the completion
section renders and distinguishes "process finished" from "task completed and
merged"."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _at_execution_center() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "execution_center"
    at.run()
    return at


def _completed_run(db_path):
    task = runtime_db.create_task(db_path, project="AICC", title="Autonomy task", task_type="implementation")
    session = runtime_db.create_session(db_path, task_id=task["id"], project="AICC", repository_path="/tmp/x")
    run = runtime_db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AICC",
        task_type="implementation", repository_path="/tmp/x", prompt="p", is_resume=False,
        expected_branch="task/x",
    )
    v = run["version"]
    for state in ("QUEUED", "RUNNING", "COMPLETED"):
        run = runtime_db.update_run_state(db_path, run["id"], expected_version=v, new_state=state)
        v = run["version"]
    return run


def _seed_completion(db_path, run, *, state, **fields):
    row = runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/x", completion_state=state, branch="task/x", base_branch="main",
        head_commit="abcdef1234567890", merge_mode="manual", merge_method="squash",
    )
    if fields:
        runtime_db.update_completion(db_path, run["id"], expected_version=row["version"], fields=fields)


# 21 -------------------------------------------------------------------------
def test_finished_process_but_unmerged_not_shown_as_done():
    api = runtime_api.ExecutionCenterAPI()
    run = _completed_run(api.db_path)
    _seed_completion(api.db_path, run, state="AWAITING_MERGE", pull_request_number=7,
                     pull_request_state="OPEN", last_reason_code="AWAITING_MANUAL_MERGE",
                     recommended_action="Awaiting manual merge.")
    at = _at_execution_center()
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "процесс завершён, но задача ещё не смёржена" in captions
    assert "задача завершена и смёржена" not in captions
    assert any(button.label == "Сделать мердж" for button in at.button)


# 22 -------------------------------------------------------------------------
def test_closed_unmerged_marked_requires_attention():
    api = runtime_api.ExecutionCenterAPI()
    run = _completed_run(api.db_path)
    _seed_completion(api.db_path, run, state="REQUIRES_ATTENTION", requires_human=1, is_recoverable=1,
                     last_reason_code="RECOVERY_DISABLED",
                     recommended_action="Pull request was closed without merging.")
    at = _at_execution_center()
    assert not at.exception
    text = " ".join(c.value for c in at.caption) + " ".join(str(m.value) for m in at.markdown)
    assert "Requires Attention" in text


# 23 -------------------------------------------------------------------------
def test_pr_number_and_recommended_action_displayed():
    api = runtime_api.ExecutionCenterAPI()
    run = _completed_run(api.db_path)
    _seed_completion(api.db_path, run, state="PR_CLOSED_UNMERGED", pull_request_number=42,
                     pull_request_url="https://github.com/x/y/pull/42", pull_request_state="CLOSED",
                     replaced_pull_request_number=None,
                     recommended_action="Recreating the remote branch and opening a replacement pull request.")
    at = _at_execution_center()
    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    warnings = " ".join(str(w.value) for w in at.warning) + " ".join(str(i.value) for i in at.info)
    assert "#42" in body
    assert "Recreating the remote branch" in warnings


# 24 -------------------------------------------------------------------------
def test_completion_renders_safely_with_missing_optional_data():
    api = runtime_api.ExecutionCenterAPI()
    run = _completed_run(api.db_path)
    # A bare completion row: no PR, no merge commit, no validation summary.
    _seed_completion(api.db_path, run, state="VALIDATING_RESULT")
    at = _at_execution_center()
    assert not at.exception  # must not crash on None optional fields


def test_completed_task_shown_as_done_and_merged():
    api = runtime_api.ExecutionCenterAPI()
    run = _completed_run(api.db_path)
    _seed_completion(api.db_path, run, state="COMPLETED", pull_request_number=7,
                     pull_request_state="MERGED", merge_commit="deadbeefcafebabe",
                     recommended_action="Task completed and merged into the target branch.")
    at = _at_execution_center()
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "задача завершена и смёржена" in captions
