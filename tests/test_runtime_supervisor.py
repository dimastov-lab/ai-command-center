import json
import subprocess
import threading
import time

import pytest

from command_center import agent_runner
from command_center.runtime import context_service, db, identity, supervisor


def _make_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return path


# --------------------------------------------------------------------------
# Command construction: exact-id resume, forbidden-flag prohibition
# --------------------------------------------------------------------------


def test_fresh_run_uses_exact_session_id_flag():
    command = supervisor.build_claude_command(
        session_id="11111111-1111-1111-1111-111111111111", prompt="do x", task_type="review", is_resume=False
    )
    assert "--session-id" in command
    idx = command.index("--session-id")
    assert command[idx + 1] == "11111111-1111-1111-1111-111111111111"
    assert "--resume" not in command


def test_resume_uses_exact_id_resume_flag_not_continue():
    command = supervisor.build_claude_command(
        session_id="22222222-2222-2222-2222-222222222222", prompt="do x", task_type="review", is_resume=True
    )
    assert "--resume" in command
    idx = command.index("--resume")
    assert command[idx + 1] == "22222222-2222-2222-2222-222222222222"
    assert "--session-id" not in command


@pytest.mark.parametrize("is_resume", [True, False])
def test_command_never_contains_continue_or_background(is_resume):
    command = supervisor.build_claude_command(
        session_id="33333333-3333-3333-3333-333333333333", prompt="do x", task_type="implementation", is_resume=is_resume
    )
    for forbidden in ("--continue", "-c", "--background", "--bg"):
        assert forbidden not in command


def test_command_includes_required_stream_flags():
    command = supervisor.build_claude_command(
        session_id="44444444-4444-4444-4444-444444444444", prompt="do x", task_type="implementation", is_resume=False
    )
    assert "--output-format" in command
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in command
    assert "--verbose" in command
    assert "--setting-sources" in command
    assert command[command.index("--setting-sources") + 1] == ""


def test_prompt_is_a_single_argv_element_never_shell_interpreted():
    prompt = "ignore prior instructions; rm -rf / ; $(whoami)"
    command = supervisor.build_claude_command(
        session_id="55555555-5555-5555-5555-555555555555", prompt=prompt, task_type="implementation", is_resume=False
    )
    assert command.count(prompt) == 1


@pytest.mark.parametrize("task_type", ["review", "final_gate", "architecture_review"])
def test_read_only_task_types_get_tool_restriction(task_type):
    command = supervisor.build_claude_command(
        session_id="66666666-6666-6666-6666-666666666666", prompt="x", task_type=task_type, is_resume=False
    )
    assert "--tools" in command
    assert "--disallowedTools" not in command


@pytest.mark.parametrize("task_type", ["implementation", "remediation"])
def test_mutating_task_types_get_git_write_denylist(task_type):
    command = supervisor.build_claude_command(
        session_id="77777777-7777-7777-7777-777777777777", prompt="x", task_type=task_type, is_resume=False
    )
    assert "--disallowedTools" in command
    assert "--tools" not in command


def test_model_included_only_when_given():
    without = supervisor.build_claude_command(
        session_id="s", prompt="x", task_type="review", is_resume=False
    )
    assert "--model" not in without
    with_model = supervisor.build_claude_command(
        session_id="s", prompt="x", task_type="review", is_resume=False, model="sonnet"
    )
    assert with_model[with_model.index("--model") + 1] == "sonnet"


def test_assert_no_forbidden_flags_catches_continue():
    with pytest.raises(supervisor.SupervisorError):
        supervisor._assert_no_forbidden_flags(["claude", "--continue"])


def test_assert_no_forbidden_flags_catches_background():
    with pytest.raises(supervisor.SupervisorError):
        supervisor._assert_no_forbidden_flags(["claude", "--background"])


def test_assert_no_forbidden_flags_passes_clean_command():
    supervisor._assert_no_forbidden_flags(["claude", "--session-id", "x", "-p", "hi"])  # must not raise


# --------------------------------------------------------------------------
# start_raw() validation before any subprocess is spawned
# --------------------------------------------------------------------------


def test_start_requires_explicit_confirmation(git_repo, configure_project_repo):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    with pytest.raises(context_service.ConfirmationRequiredError):
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=False
        )
    assert db.list_runs(sup.db_path) == []


def test_start_rejects_unconfigured_repository(git_repo, configure_project_repo):
    from command_center import agent_runner

    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    with pytest.raises(agent_runner.RunnerError):
        sup.start_raw(
            project="AIOS", repository_path="/not/the/configured/path", task_type="implementation",
            prompt="p", confirmed=True,
        )


def test_start_resume_requires_existing_session(git_repo, configure_project_repo):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.SupervisorError):
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p",
            confirmed=True, is_resume=True, session_id="no-such-session",
        )


# --------------------------------------------------------------------------
# Full launch lifecycle via the real (fake) subprocess
# --------------------------------------------------------------------------


def test_full_run_completes_and_persists_all_stream_event_types(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    assert run["state"] == "RUNNING"
    assert run["pid"] is not None
    assert run["process_start_identity"]

    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"
    assert final["exit_code"] == 0
    assert final["started_at"] and final["completed_at"]

    events = db.list_run_events(sup.db_path, run["id"])
    event_types = [e["event_type"] for e in events]
    assert "lifecycle" in event_types
    assert "assistant_partial" in event_types
    assert "assistant_message" in event_types
    assert "result" in event_types

    # Genuine ordering assertions (not a tautological self-sort): the events
    # table's `seq` order must reflect the real order things happened in.
    def _first_seq(event_type):
        return next(e["seq"] for e in events if e["event_type"] == event_type)

    lifecycle_seqs = [e["seq"] for e in events if e["event_type"] == "lifecycle"]
    process_started_seq = min(lifecycle_seqs)
    process_exited_seq = max(lifecycle_seqs)
    assert events[0]["event_type"] == "lifecycle" and events[0]["seq"] == process_started_seq, (
        "the very first persisted event must be the 'process_started' lifecycle event"
    )
    assert events[-1]["event_type"] == "lifecycle" and events[-1]["seq"] == process_exited_seq, (
        "the very last persisted event must be the 'process_exited' lifecycle event"
    )
    # fake_claude.py's DEFAULT_LINES order is: system init, stream_event
    # (assistant_partial), assistant message, result — the persisted seq
    # order must match that real emission order.
    assert process_started_seq < _first_seq("assistant_partial") < _first_seq("assistant_message") < _first_seq(
        "result"
    ) < process_exited_seq

    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)

    report = db.get_report(sup.db_path, run["id"])
    assert report is not None


def test_incremental_persistence_happens_before_process_exits(git_repo, configure_project_repo, fake_claude):
    """Events must land in the database while the process is still running,
    not only once it exits — this is what "do not wait until process exit
    before storing output" means operationally."""
    configure_project_repo("AIOS", git_repo)
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "3"
    fake_claude["FAKE_CLAUDE_DELAY"] = "0.01"

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )

    deadline = time.monotonic() + 5
    saw_result_event_while_running = False
    while time.monotonic() < deadline:
        current = db.get_run(sup.db_path, run["id"])
        events = db.list_run_events(sup.db_path, run["id"])
        if current["state"] == "RUNNING" and any(e["event_type"] == "result" for e in events):
            saw_result_event_while_running = True
            break
        time.sleep(0.05)

    assert saw_result_event_while_running, "the 'result' event must be persisted before the process exits"

    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"


def test_malformed_stream_line_preserved_as_diagnostic_and_run_still_completes(
    git_repo, configure_project_repo, fake_claude
):
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        "THIS IS NOT JSON {{{",
        json.dumps({"type": "result", "result": "done anyway"}),
    ]
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(lines)
    configure_project_repo("AIOS", git_repo)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED", "a malformed line must not crash the supervisor or fail the run"

    events = db.list_run_events(sup.db_path, run["id"])
    malformed = [e for e in events if e["event_type"] == "malformed"]
    assert len(malformed) == 1
    assert "NOT JSON" in malformed[0]["payload"]["raw"]


def test_report_never_truncates_large_assistant_output(git_repo, configure_project_repo, fake_claude):
    huge_text = "X" * 200_000
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": huge_text}]}}),
        json.dumps({"type": "result", "result": "done"}),
    ]
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(lines)
    configure_project_repo("AIOS", git_repo)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"

    report = db.get_report(sup.db_path, run["id"])
    from command_center.runtime import reports

    content = (reports.REPORTS_ROOT.parent / report["path"]).read_text(encoding="utf-8")
    assert huge_text in content


def test_stderr_lines_are_persisted(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_STDERR"] = "a warning from claude"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    sup.wait_for_run(run["id"], timeout=10)
    events = db.list_run_events(sup.db_path, run["id"])
    stderr_events = [e for e in events if e["event_type"] == "stderr_line"]
    assert any("a warning from claude" in e["payload"]["line"] for e in stderr_events)


def test_nonzero_exit_code_marks_run_failed(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXIT_CODE"] = "1"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "FAILED"
    assert final["exit_code"] == 1


def test_resume_reuses_session_and_increments_sequence(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    first = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="first",
        confirmed=True,
    )
    sup.wait_for_run(first["id"], timeout=10)

    second = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="second",
        confirmed=True, is_resume=True, session_id=first["session_id"],
    )
    assert second["session_id"] == first["session_id"]
    assert second["sequence"] == first["sequence"] + 1
    assert second["is_resume"] == 1

    sessions = db.list_sessions(sup.db_path)
    assert len({s["id"] for s in sessions}) == 1

    sup.wait_for_run(second["id"], timeout=10)


# --------------------------------------------------------------------------
# Launch failure (Popen itself fails)
# --------------------------------------------------------------------------


def test_popen_failure_marks_run_failed_without_raising(git_repo, configure_project_repo, monkeypatch):
    configure_project_repo("AIOS", git_repo)

    def raise_oserror(*args, **kwargs):
        raise OSError("claude binary not found")

    monkeypatch.setattr(supervisor.subprocess, "Popen", raise_oserror)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    assert run["state"] == "FAILED"
    assert sup.active_run_ids() == []


# --------------------------------------------------------------------------
# Cancellation: confirmation, process-group SIGTERM/SIGKILL, no orphans
# --------------------------------------------------------------------------


def test_cancel_requires_explicit_confirmation(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    try:
        with pytest.raises(context_service.ConfirmationRequiredError):
            sup.cancel(run["id"], confirmed=False)
    finally:
        sup.cancel(run["id"], confirmed=True, grace_seconds=2)


def test_cancel_on_unknown_run_raises(git_repo, configure_project_repo):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.SupervisorError):
        sup.cancel("no-such-run", confirmed=True)


def test_cancel_graceful_sigterm_exit_within_grace_period(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"  # would run long, but responds to SIGTERM immediately
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    time.sleep(0.3)
    result = sup.cancel(run["id"], confirmed=True, grace_seconds=5)
    assert result["state"] == "CANCELLED"

    events = db.list_run_events(sup.db_path, run["id"])
    lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
    assert "cancel_requested" in lifecycles
    assert "cancel_sigterm_sent" in lifecycles
    assert "cancel_sigkill_sent" not in lifecycles, "a process that dies from SIGTERM must not also receive SIGKILL"


def test_cancel_escalates_to_sigkill_after_grace_period_when_sigterm_ignored(
    git_repo, configure_project_repo, fake_claude
):
    fake_claude["FAKE_CLAUDE_IGNORE_SIGTERM"] = "1"
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "30"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    time.sleep(0.3)

    started = time.monotonic()
    result = sup.cancel(run["id"], confirmed=True, grace_seconds=1)
    elapsed = time.monotonic() - started

    assert result["state"] == "CANCELLED"
    assert elapsed >= 1, "SIGKILL must not fire before the grace period elapses"

    events = db.list_run_events(sup.db_path, run["id"])
    lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
    assert "cancel_sigterm_sent" in lifecycles
    assert "cancel_sigkill_sent" in lifecycles


def test_cancel_leaves_no_orphaned_child_process(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_IGNORE_SIGTERM"] = "1"
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "30"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    pid = run["pid"]
    time.sleep(0.3)
    sup.cancel(run["id"], confirmed=True, grace_seconds=1)
    time.sleep(0.3)
    assert identity.process_exists(pid) is False


def test_cancel_preserves_output_received_before_cancellation(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [json.dumps({"type": "system", "subtype": "init"}), json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "partial progress"}]}})]
    )
    fake_claude["FAKE_CLAUDE_DELAY"] = "0.05"
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    time.sleep(0.5)
    sup.cancel(run["id"], confirmed=True, grace_seconds=2)

    events = db.list_run_events(sup.db_path, run["id"])
    assert any(e["event_type"] == "assistant_message" for e in events), (
        "output already received before cancellation must be preserved"
    )


def test_cancel_never_runs_git_restore_and_flags_working_tree_change(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = "f.txt"
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    fake_claude["FAKE_CLAUDE_IGNORE_SIGTERM"] = "1"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    # Give the fake process time to run past its lines and touch the file.
    time.sleep(0.5)
    result = sup.cancel(run["id"], confirmed=True, grace_seconds=1)
    assert result["state"] == "CANCELLED"
    assert result["working_tree_changed"] == 1

    # The file must still show the modification — nothing here ever runs
    # `git restore`/`reset`/`clean`.
    assert "modified by fake_claude" in (git_repo / "f.txt").read_text()

    events = db.list_run_events(sup.db_path, run["id"])
    lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
    assert "cancellation_working_tree_changed_requires_inspection" in lifecycles


def test_cancel_on_already_terminal_run_raises_and_does_not_resignal(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"

    with pytest.raises(supervisor.SupervisorError):
        sup.cancel(run["id"], confirmed=True)

    # Must not have silently moved the finished run back to RUNNING/CANCELLED.
    assert db.get_run(sup.db_path, run["id"])["state"] == "COMPLETED"


# --------------------------------------------------------------------------
# F3: timeout watchdog — monotonic deadline, same SIGTERM/grace/SIGKILL path
# --------------------------------------------------------------------------


def test_timeout_none_means_no_automatic_timeout(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True,
        timeout_seconds=None,
    )
    assert run["timeout_seconds"] is None
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED", "a run with no timeout must complete normally, never time out"
    events = db.list_run_events(sup.db_path, run["id"])
    assert not any(e["payload"].get("lifecycle") == "timeout_exceeded" for e in events if e["event_type"] == "lifecycle")


def test_timeout_graceful_process_exits_on_sigterm(git_repo, configure_project_repo, fake_claude):
    """The watchdog fires SIGTERM at the deadline; a process that responds
    promptly must reach FAILED/timeout without ever needing SIGKILL."""
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"  # would run long past the timeout without the watchdog
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True,
        timeout_seconds=1,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "timeout"

    events = db.list_run_events(sup.db_path, run["id"])
    lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
    assert "timeout_exceeded" in lifecycles
    assert "timeout_sigterm_sent" in lifecycles
    assert "timeout_sigkill_sent" not in lifecycles, "a process that dies from SIGTERM must not also receive SIGKILL"


def test_timeout_escalates_to_sigkill_when_sigterm_ignored(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_IGNORE_SIGTERM"] = "1"
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "30"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()

    started = time.monotonic()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True,
        timeout_seconds=1,
    )
    pid = run["pid"]
    final = sup.wait_for_run(run["id"], timeout=15)
    elapsed = time.monotonic() - started

    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "timeout"
    assert elapsed >= 1, "SIGKILL must not fire before the timeout deadline elapses"

    events = db.list_run_events(sup.db_path, run["id"])
    lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
    assert "timeout_sigterm_sent" in lifecycles
    assert "timeout_sigkill_sent" in lifecycles

    from command_center.runtime import identity

    assert identity.process_exists(pid) is False, "no orphan after a forced timeout kill"


def test_timeout_preserves_output_received_before_the_deadline(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "partial work"}]}}),
        ]
    )
    fake_claude["FAKE_CLAUDE_DELAY"] = "0.05"
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "10"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True,
        timeout_seconds=1,
    )
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "timeout"

    events = db.list_run_events(sup.db_path, run["id"])
    assert any(e["event_type"] == "assistant_message" for e in events), (
        "output received before the timeout fired must be preserved"
    )


def test_timeout_does_not_fire_after_natural_completion(git_repo, configure_project_repo, fake_claude):
    """A generous timeout on a fast run must never fire — this proves the
    watchdog thread exits cleanly once the run finishes on its own."""
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True,
        timeout_seconds=60,
    )
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"
    assert final["failure_reason"] is None


# --------------------------------------------------------------------------
# Workspace locking — a workspace can have at most one active run, enforced
# atomically by `db.create_run(enforce_workspace_lock=True)` (see
# tests/test_runtime_db.py for the db-layer race proof); these tests cover
# the Supervisor-facing contract (`WorkspaceLockedError`) and concurrent runs
# across *different* workspaces still working normally.
# --------------------------------------------------------------------------


def test_workspace_locked_error_is_a_supervisor_error():
    """Every existing caller that already catches `supervisor.SupervisorError`
    (e.g. `app.py`'s launch handlers) must catch this without a new except
    clause."""
    assert issubclass(supervisor.WorkspaceLockedError, supervisor.SupervisorError)


def test_start_raw_raises_workspace_locked_error_when_workspace_already_active(
    git_repo, configure_project_repo, fake_claude
):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    first = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p1", confirmed=True
    )
    try:
        with pytest.raises(supervisor.WorkspaceLockedError) as excinfo:
            sup.start_raw(
                project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p2",
                confirmed=True,
            )
        assert excinfo.value.conflicting_run["id"] == first["id"]
        # The rejected second launch must never have spawned a process or
        # created a second run row for this workspace.
        active = db.list_runs(sup.db_path, states=db.EXECUTION_CENTER_ACTIVE_STATES)
        assert [r["id"] for r in active] == [first["id"]]
    finally:
        sup.cancel(first["id"], confirmed=True, grace_seconds=2)


def test_start_raw_allows_relaunch_of_same_workspace_after_prior_run_completes(
    git_repo, configure_project_repo, fake_claude
):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    first = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p1", confirmed=True
    )
    sup.wait_for_run(first["id"], timeout=10)

    second = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p2", confirmed=True
    )
    assert second["state"] == "RUNNING"
    sup.wait_for_run(second["id"], timeout=10)


def test_start_raw_allows_concurrent_runs_against_different_workspaces(tmp_path, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "2"
    repo_a = _make_git_repo(tmp_path / "repo_a")
    repo_b = _make_git_repo(tmp_path / "repo_b")
    sup = supervisor.Supervisor()
    run_a = sup.start_raw(
        project="AIOS", repository_path=str(repo_a), task_type="implementation", prompt="p", confirmed=True,
        repository_already_validated=True,
    )
    run_b = sup.start_raw(
        project="AIOS", repository_path=str(repo_b), task_type="implementation", prompt="p", confirmed=True,
        repository_already_validated=True,
    )
    assert run_a["state"] == "RUNNING"
    assert run_b["state"] == "RUNNING"
    sup.cancel(run_a["id"], confirmed=True, grace_seconds=2)
    sup.cancel(run_b["id"], confirmed=True, grace_seconds=2)


def test_concurrent_start_raw_against_same_workspace_exactly_one_wins(git_repo, configure_project_repo, fake_claude):
    """Two genuinely concurrent `start_raw` calls (not just two sequential
    ones) against the same workspace — proves the lock is race-free at the
    Supervisor layer, not just a sequential pre-flight check."""
    configure_project_repo("AIOS", git_repo)
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "2"
    sup = supervisor.Supervisor()

    winners: list[dict] = []
    losers: list[supervisor.WorkspaceLockedError] = []
    lock = threading.Lock()

    def attempt(idx: int) -> None:
        try:
            run = sup.start_raw(
                project="AIOS", repository_path=str(git_repo), task_type="implementation",
                prompt=f"p{idx}", confirmed=True,
            )
            with lock:
                winners.append(run)
        except supervisor.WorkspaceLockedError as exc:
            with lock:
                losers.append(exc)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(winners) == 1, f"exactly one concurrent launch must win the workspace lock, got {winners}"
    assert len(losers) == 5
    winner = winners[0]
    assert winner["state"] == "RUNNING"
    assert all(exc.conflicting_run["id"] == winner["id"] for exc in losers)

    sup.cancel(winner["id"], confirmed=True, grace_seconds=2)


# --------------------------------------------------------------------------
# Crash recovery: `self._launching` protects an in-flight (QUEUED, not yet
# `Popen`'d) run of *this* instance from a concurrent `reconcile()` call —
# see tests/test_runtime_reconciliation.py for reconcile()'s own widened
# PREPARED/QUEUED scope.
# --------------------------------------------------------------------------


def test_launching_set_is_cleared_after_a_successful_launch(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    assert run["id"] not in sup._launching
    assert run["id"] in sup.active_run_ids()
    sup.wait_for_run(run["id"], timeout=10)


def test_launching_set_is_cleared_after_a_popen_failure(git_repo, configure_project_repo, monkeypatch):
    configure_project_repo("AIOS", git_repo)

    def raise_oserror(*args, **kwargs):
        raise OSError("claude binary not found")

    monkeypatch.setattr(supervisor.subprocess, "Popen", raise_oserror)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    assert run["state"] == "FAILED"
    assert run["id"] not in sup._launching


def test_launching_set_is_cleared_after_a_prepared_to_queued_transition_failure(
    git_repo, configure_project_repo, monkeypatch
):
    """Regression test for the window this hardening closes: `self._launching`
    must already hold the run id at the moment the PREPARED -> QUEUED
    transition is attempted — proving registration happens immediately after
    `db.create_run` returns, not after `QUEUED` is persisted — and must be
    cleared if that transition itself raises, with the original exception
    propagating untouched and no PREPARED row silently left both unguarded
    and un-classifiable until the next `reconcile()`."""
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()

    observed = {}
    original_update_run_state = supervisor.db.update_run_state

    def failing_update_run_state(db_path, run_id, *, expected_version, new_state, fields=None):
        if new_state == "QUEUED":
            observed["run_id_guarded_during_transition"] = run_id in sup._launching
            raise RuntimeError("simulated PREPARED -> QUEUED failure")
        return original_update_run_state(
            db_path, run_id, expected_version=expected_version, new_state=new_state, fields=fields
        )

    monkeypatch.setattr(supervisor.db, "update_run_state", failing_update_run_state)

    with pytest.raises(RuntimeError, match="simulated PREPARED -> QUEUED failure"):
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
        )

    assert observed["run_id_guarded_during_transition"] is True
    assert not sup._launching

    runs = db.list_runs(sup.db_path, states=db.EXECUTION_CENTER_ACTIVE_STATES)
    assert len(runs) == 1
    assert runs[0]["state"] == "PREPARED"


def test_concurrent_reconcile_cannot_claim_a_just_created_live_launch(
    git_repo, configure_project_repo, fake_claude, monkeypatch
):
    """Exercise the real create/register boundary, not a manually seeded set.

    The committed PREPARED row is held at the return boundary of create_run
    while another thread calls reconcile(). Reconciliation must wait until the
    launcher has registered ownership, then skip the row instead of changing it
    to INTERRUPTED out from under PREPARED -> QUEUED.
    """
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    real_create_run = supervisor.db.create_run
    row_committed = threading.Event()
    release_create = threading.Event()
    launch_result = {}
    reconcile_result = {}

    def paused_create_run(*args, **kwargs):
        run = real_create_run(*args, **kwargs)
        row_committed.set()
        assert release_create.wait(timeout=5), "test did not release create_run"
        return run

    monkeypatch.setattr(supervisor.db, "create_run", paused_create_run)

    def launch():
        try:
            launch_result["run"] = sup.start_raw(
                project="AIOS",
                repository_path=str(git_repo),
                task_type="implementation",
                prompt="p",
                confirmed=True,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            launch_result["error"] = exc

    def reconcile():
        reconcile_result["outcomes"] = sup.reconcile()

    launch_thread = threading.Thread(target=launch)
    launch_thread.start()
    assert row_committed.wait(timeout=5), "launch never committed its PREPARED row"

    reconcile_thread = threading.Thread(target=reconcile)
    reconcile_thread.start()
    try:
        # Give the concurrent call an opportunity to reach the boundary. On
        # the regressed implementation it immediately claims the PREPARED row.
        time.sleep(0.1)
    finally:
        release_create.set()

    launch_thread.join(timeout=10)
    reconcile_thread.join(timeout=10)
    assert not launch_thread.is_alive()
    assert not reconcile_thread.is_alive()
    assert "error" not in launch_result, launch_result.get("error")
    assert reconcile_result["outcomes"] == []

    run = launch_result["run"]
    assert run["state"] == "RUNNING"
    assert sup.wait_for_run(run["id"], timeout=10)["state"] == "COMPLETED"


# --------------------------------------------------------------------------
# --permission-mode — the empirically-confirmed root cause of the reported
# defect: without it, the real `claude` CLI denies `Write`/`Edit` tool calls
# in headless `-p` mode while the process itself still exits 0 (see
# `agent_runner`'s profile docstring for the verification method/evidence).
# --------------------------------------------------------------------------


def test_build_claude_command_includes_permission_mode_for_trusted_development():
    command = supervisor.build_claude_command(
        session_id="88888888-8888-8888-8888-888888888888", prompt="x", task_type="implementation", is_resume=False
    )
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == agent_runner.PERMISSION_MODE_BY_PROFILE[
        agent_runner.PROFILE_TRUSTED_DEVELOPMENT
    ]


def test_build_claude_command_includes_permission_mode_for_read_only():
    command = supervisor.build_claude_command(
        session_id="99999999-9999-9999-9999-999999999999", prompt="x", task_type="review", is_resume=False
    )
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == agent_runner.PERMISSION_MODE_BY_PROFILE[
        agent_runner.PROFILE_READ_ONLY
    ]


# --------------------------------------------------------------------------
# EvaluatingResult end-to-end: exit_code == 0 is not the whole story — a
# permission denial or an unchanged working tree must not be recorded
# COMPLETED. Regression tests 1/2/5/6 from the remediation brief.
# --------------------------------------------------------------------------


def test_exit_zero_with_permission_denial_is_blocked_not_completed(git_repo, configure_project_repo, fake_claude):
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "result",
                "result": "DONE",
                "permission_denials": [{"tool_name": "Write", "tool_use_id": "x", "tool_input": {}}],
            }
        ),
    ]
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(lines)
    configure_project_repo("AIOS", git_repo)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["exit_code"] == 0, "the CLI process itself must still exit 0 in this scenario"
    assert final["state"] == "FAILED", "a permission-denied run must never be recorded COMPLETED"
    assert final["failure_reason"] == "blocked:permission_denied:Write"


def test_exit_zero_with_blocked_final_response_is_blocked_not_completed(git_repo, configure_project_repo, fake_claude):
    """Required regression test 1: exit_code=0 plus an explicit blocked final
    response -> Blocked, even with no structured `permission_denials`
    evidence at all (Required fix 6's text-classifier fallback)."""
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "result", "result": "I cannot execute this task: Bash is unavailable to me."}),
    ]
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(lines)
    configure_project_repo("AIOS", git_repo)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["exit_code"] == 0
    assert final["state"] == "FAILED"
    assert final["failure_reason"].startswith("blocked:final_response:")


def test_exit_zero_with_unchanged_working_tree_is_incomplete_not_completed(
    git_repo, configure_project_repo, fake_claude
):
    """Required regression test 5: task requiring changes plus
    working_tree_changed=false -> Incomplete."""
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = ""  # override the fixture default: no file touched this run
    configure_project_repo("AIOS", git_repo)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["exit_code"] == 0
    assert final["working_tree_changed"] == 0
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "incomplete:working_tree_unchanged"


def test_exit_zero_read_only_task_type_completes_even_without_working_tree_change(
    git_repo, configure_project_repo, fake_claude
):
    """A `review` (read-only) run is never expected to change the working
    tree — an unchanged tree must not make it `Incomplete`."""
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = ""
    configure_project_repo("AIOS", git_repo)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="review", prompt="review this",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["state"] == "COMPLETED"


def test_exit_zero_with_changes_and_no_blockers_is_genuinely_completed(
    git_repo, configure_project_repo, fake_claude
):
    """The positive case: nothing blocked, changes were made -> COMPLETED,
    exactly as before this remediation. Guards against the classifier being
    so strict it never lets a real success through."""
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["exit_code"] == 0
    assert final["working_tree_changed"] == 1
    assert final["state"] == "COMPLETED"
    assert final["failure_reason"] is None


# --------------------------------------------------------------------------
# Linked git worktrees — git commands must work normally via Bash from
# inside a linked worktree, without any special-casing in this project's
# own code (git resolves `.git`-file-pointer worktrees transparently on its
# own; this only regresses if Bash/permission-mode is wrong for the run).
# --------------------------------------------------------------------------


def test_git_snapshot_works_from_inside_a_linked_worktree(git_repo, tmp_path):
    worktree_path = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/from-worktree", str(worktree_path)],
        cwd=git_repo, check=True, capture_output=True,
    )

    snapshot = agent_runner.git_snapshot(worktree_path)

    assert snapshot["is_git_repo"] is True
    assert snapshot["branch"] == "feature/from-worktree"
    assert snapshot["status_summary"] == "(чисто)"

    worktrees = subprocess.run(
        ["git", "worktree", "list"], cwd=worktree_path, check=True, capture_output=True, text=True,
    ).stdout
    assert str(git_repo) in worktrees
    assert str(worktree_path) in worktrees


def test_supervisor_run_completes_from_inside_a_linked_worktree(git_repo, tmp_path, fake_claude, monkeypatch):
    """A v2 run launched with `repository_path` pointing at a linked
    worktree (not the primary checkout) must supervise normally end to end —
    `Popen(..., cwd=repo_path)` plus the run's own `git_snapshot` calls need
    no special-casing for a linked worktree's `.git` *file* (vs. the primary
    checkout's `.git` *directory*)."""
    worktree_path = tmp_path / "linked-worktree-2"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/supervised-from-worktree", str(worktree_path)],
        cwd=git_repo, check=True, capture_output=True,
    )
    assert (worktree_path / ".git").is_file(), "a linked worktree's .git is a file, not a directory"

    from command_center import project_config

    def fake_get_project_config(pid, _repo_path=str(worktree_path)):
        cfg = project_config.default_project_config(pid)
        cfg["repository_path"] = _repo_path
        return cfg

    monkeypatch.setattr(project_config, "get_project_config", fake_get_project_config)
    monkeypatch.setattr(agent_runner.project_config, "get_project_config", fake_get_project_config)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(worktree_path), task_type="implementation", prompt="do a thing",
        confirmed=True,
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["state"] == "COMPLETED"
    reloaded = db.get_run(sup.db_path, run["id"])
    assert reloaded["state"] == "COMPLETED"
