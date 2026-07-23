from __future__ import annotations

import ast
import json
import subprocess
import time
from pathlib import Path

import pytest

from command_center import launch, launch_service
from command_center.runtime import api as runtime_api
from command_center.runtime import db, providers, session_view, supervisor


def _add_worktree(repo: Path, target: Path, branch: str = "feature/codex-test") -> Path:
    subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(target), "HEAD"], cwd=repo, check=True)
    return target


def _start_codex(sup, *, canonical, worktree, branch="feature/codex-test", **kwargs):
    return sup.start_raw(
        project="AIOS",
        repository_path=str(worktree),
        canonical_repository_path=str(canonical),
        expected_branch=branch,
        task_type=kwargs.pop("task_type", "implementation"),
        prompt=kwargs.pop("prompt", "implement safely"),
        confirmed=True,
        repository_already_validated=True,
        executor_id="codex",
        **kwargs,
    )


def test_codex_discovery_and_version_probe_use_fake_only(fake_codex):
    availability = providers.get_provider("codex").availability()
    assert availability.available is True
    assert availability.code == "usable"
    assert availability.executable == str(fake_codex)
    assert availability.version == "codex-cli 0.145.0-fake"


def test_codex_missing_executable_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("AICC_CODEX_BINARY", str(tmp_path / "missing"))
    availability = providers.get_provider("codex").availability()
    assert availability.available is False
    assert availability.code == "executable_missing"
    assert "install" in availability.message.lower() or "configure" in availability.message.lower()


def test_codex_unsupported_interface_fails_closed(fake_codex, monkeypatch):
    original = providers._probe

    def probe(executable, args, *, provider_id):
        if args == ["exec", "--help"]:
            return True, "old incompatible help"
        return original(executable, args, provider_id=provider_id)

    monkeypatch.setattr(providers, "_probe", probe)
    availability = providers.get_provider("codex").availability()
    assert availability.available is False
    assert availability.code == "unsupported_interface"


def test_codex_version_probe_failure_is_distinct(fake_codex, monkeypatch):
    monkeypatch.setattr(
        providers,
        "_probe",
        lambda executable, args, *, provider_id: (False, "codex version/interface probe failed (exit 9)"),
    )
    availability = providers.get_provider("codex").availability()
    assert availability.available is False
    assert availability.code == "version_probe_failed"


def test_codex_argv_is_fixed_and_prompt_is_stdin_only(fake_codex, git_repo):
    prompt = "secret; $(whoami); rm -rf /"
    spec = providers.get_provider("codex").build_launch(
        repository_path=git_repo,
        session_id="unused",
        prompt=prompt,
        task_type="implementation",
        is_resume=False,
        model=None,
    )
    assert spec.argv == (
        str(fake_codex), "exec", "--json", "--color", "never", "--sandbox",
        "workspace-write", "--cd", str(git_repo), "-",
    )
    assert prompt not in spec.argv
    assert spec.stdin_text == prompt
    assert spec.audit_metadata["prompt_transport"] == "stdin"
    assert "secret" not in json.dumps(spec.audit_metadata)


def test_supervisor_never_uses_shell_true_for_provider_launches():
    source = Path(supervisor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    popen_calls = [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
    assert popen_calls
    assert all(
        any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in call.keywords)
        for call in popen_calls
    )


def test_codex_rejects_canonical_checkout(fake_codex, git_repo):
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.SupervisorError, match="canonical checkout"):
        _start_codex(sup, canonical=git_repo, worktree=git_repo)
    assert db.list_runs(sup.db_path) == []


def test_codex_rejects_wrong_or_missing_task_branch(fake_codex, git_repo, tmp_path):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.SupervisorError, match="intended task branch"):
        _start_codex(sup, canonical=git_repo, worktree=worktree, branch=None)
    with pytest.raises(supervisor.SupervisorError, match="Unsafe Codex target worktree"):
        _start_codex(sup, canonical=git_repo, worktree=worktree, branch="feature/wrong")


def test_codex_launch_handshake_prompt_transport_and_audit(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    capture = tmp_path / "prompt.txt"
    monkeypatch.setenv("FAKE_CODEX_PROMPT_CAPTURE", str(capture))
    prompt = "sensitive prompt that must not be argv-visible"
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree, prompt=prompt)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"
    assert final["provider_id"] == "codex"
    assert final["prompt"].startswith("[redacted:")
    assert prompt not in final["command_json"]
    assert prompt not in db.get_task(sup.db_path, final["task_id"])["title"]
    assert capture.read_text(encoding="utf-8") == prompt
    metadata = json.loads(final["provider_metadata_json"])
    assert metadata["provider_id"] == "codex"
    assert metadata["prompt_transport"] == "stdin"
    assert final["first_output_at"]
    events = db.list_run_events(sup.db_path, run["id"])
    assert any(event["payload"].get("lifecycle") == "prompt_delivered" for event in events)
    assert any(event["event_type"] == "assistant_message" for event in events)
    assert session_view.derive_status(final) == session_view.STATUS_COMPLETED


def test_codex_duplicate_prevention_and_cancellation(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_EXTRA_SLEEP", "5")
    sup = supervisor.Supervisor()
    first = _start_codex(sup, canonical=git_repo, worktree=worktree)
    with pytest.raises(supervisor.WorkspaceLockedError):
        _start_codex(sup, canonical=git_repo, worktree=worktree)
    cancelled = sup.cancel(first["id"], confirmed=True, grace_seconds=1)
    assert cancelled["state"] == "CANCELLED"


def test_cancellation_refuses_unverifiable_reused_pid(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_EXTRA_SLEEP", "5")
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    real_matcher = supervisor.identity.identity_matches
    monkeypatch.setattr(supervisor.identity, "identity_matches", lambda *_args, **_kwargs: False)
    with pytest.raises(supervisor.SupervisorError, match="possibly reused PID"):
        sup.cancel(run["id"], confirmed=True, grace_seconds=0.1)
    assert db.get_run(sup.db_path, run["id"])["cancel_requested"] == 0
    monkeypatch.setattr(supervisor.identity, "identity_matches", real_matcher)
    sup.cancel(run["id"], confirmed=True, grace_seconds=1)


def test_codex_ignored_termination_escalates(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_EXTRA_SLEEP", "5")
    monkeypatch.setenv("FAKE_CODEX_IGNORE_SIGTERM", "1")
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not db.get_run(sup.db_path, run["id"])["first_output_at"]:
        time.sleep(0.02)
    final = sup.cancel(run["id"], confirmed=True, grace_seconds=0.1)
    assert final["state"] == "CANCELLED"
    events = db.list_run_events(sup.db_path, run["id"])
    assert any(event["payload"].get("lifecycle") == "cancel_sigkill_sent" for event in events)


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [("quota", "quota_limit"), ("auth", "authentication_failed"), ("startup_failure", "provider_exit_nonzero")],
)
def test_codex_failure_classification(fake_codex, git_repo, tmp_path, monkeypatch, scenario, reason):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", scenario)
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == reason
    assert any(event["event_type"] == "stderr_line" for event in db.list_run_events(sup.db_path, run["id"]))


def test_codex_malformed_output_is_persisted_without_crashing(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "malformed")
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"
    assert any(event["event_type"] == "malformed" for event in db.list_run_events(sup.db_path, run["id"]))


def test_codex_stderr_redacts_authentication_material(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_STDERR", "Authorization: Bearer secret-token api_key=sk-abcdefghijk")
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    assert sup.wait_for_run(run["id"], timeout=10)["state"] == "COMPLETED"
    serialized = json.dumps(db.list_run_events(sup.db_path, run["id"]))
    assert "secret-token" not in serialized
    assert "sk-abcdefghijk" not in serialized
    assert "[REDACTED]" in serialized


def test_codex_startup_delay_stays_starting_then_runs(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setenv("FAKE_CODEX_INITIAL_DELAY", "0.4")
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    fresh = db.get_run(sup.db_path, run["id"])
    assert fresh["state"] == "RUNNING"
    assert session_view.is_awaiting_handshake(fresh)
    assert sup.wait_for_run(run["id"], timeout=10)["state"] == "COMPLETED"


def test_codex_fake_executable_is_the_only_launched_binary(fake_codex, git_repo, tmp_path, monkeypatch):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    seen = []
    original = subprocess.Popen

    def guarded(command, *args, **kwargs):
        if command[0] == str(fake_codex):
            seen.append(list(command))
        elif Path(command[0]).name == "codex":
            raise AssertionError("a non-fixture Codex executable was invoked")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(supervisor.subprocess, "Popen", guarded)
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    assert sup.wait_for_run(run["id"], timeout=10)["state"] == "COMPLETED"
    assert seen


def test_launch_service_selects_codex_through_existing_api(
    fake_codex, git_repo, tmp_path, configure_project_repo
):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    configure_project_repo("AIOS", git_repo)
    api = runtime_api.ExecutionCenterAPI()
    validation = launch.validate_launch(
        workspace_path=str(worktree), expected_branch="feature/codex-test"
    )
    run = launch_service.execute_agent_launch_v2(
        project="AIOS",
        task_type="implementation",
        prompt="implement",
        timeout_seconds=30,
        repository_path=worktree,
        execution_center_api=api,
        confirmed=True,
        executor_id="codex",
        validation=validation,
        expected_branch="feature/codex-test",
    )
    final = api.supervisor.wait_for_run(run["id"], timeout=10)
    assert final["provider_id"] == "codex"
    assert final["state"] == "COMPLETED"


def test_codex_restart_reconciliation_never_fabricates_success(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(
        db_path, task_id=task["id"], project="AIOS", repository_path=str(tmp_path / "worktree")
    )
    run = db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path=str(tmp_path / "worktree"),
        prompt="[redacted]",
        is_resume=False,
        provider_id="codex",
    )
    outcome = supervisor.Supervisor(db_path).reconcile()
    final = db.get_run(db_path, run["id"])
    assert outcome[0]["classification"] == "INTERRUPTED"
    assert final["state"] == "INTERRUPTED"
    assert final["state"] != "COMPLETED"


def test_provider_fields_are_additive_and_default_claude(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    task = db.create_task(db_path, project="AIOS", title="t", task_type="review")
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="review",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )
    assert run["provider_id"] == "claude_code"
    assert run["provider_metadata_json"] is None


def test_unknown_provider_configuration_fails_before_persistence(git_repo):
    sup = supervisor.Supervisor()
    with pytest.raises(ValueError, match="Unknown execution provider"):
        sup.start_raw(
            project="AIOS",
            repository_path=str(git_repo),
            task_type="review",
            prompt="p",
            confirmed=True,
            repository_already_validated=True,
            executor_id="malformed-provider",
        )
    assert db.list_runs(sup.db_path) == []
