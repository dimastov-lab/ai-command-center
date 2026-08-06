"""Permanent adversarial coverage for the Codex execution-provider safety
boundaries hardened during the Founder Gate remediation.

Each test attacks one boundary the way a hostile provider, operator mistake,
or concurrent mutation would, and asserts the system fails *closed*:

* Authorization    — Codex runs only where a project explicitly allows it;
                     missing/malformed policy disables execution entirely.
* Secret hygiene   — nothing prompt-derived (or credential-shaped) reaches
                     persisted events, even when the provider echoes it.
* Result evidence  — exit code 0 is never trusted as success on its own.
* Process identity  — a launch whose process identity cannot be pinned is
                     refused rather than left unverifiable.
* Target safety    — symlinked or dirty worktrees are rejected.
* Bounded input    — oversized prompts/lines cannot exhaust persistence.
* Deletion races   — a concurrently deleted run cannot crash a reader thread.

These tests never contact a real provider (see `forbid_real_codex_subprocess`
in `conftest.py` and the `fake_codex` fixture).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from command_center import project_config, storage
from command_center.runtime import db, providers, supervisor


def _add_worktree(repo: Path, target: Path, branch: str = "feature/codex-test") -> Path:
    subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(target), "HEAD"], cwd=repo, check=True)
    return target


def _start_codex(sup, *, canonical, worktree, project="AIOS", branch="feature/codex-test", **kwargs):
    return sup.start_raw(
        project=project,
        repository_path=str(worktree),
        canonical_repository_path=str(canonical),
        expected_branch=branch,
        task_type=kwargs.pop("task_type", "review"),
        prompt=kwargs.pop("prompt", "summarize the recent changes"),
        confirmed=True,
        repository_already_validated=True,
        executor_id="codex",
        **kwargs,
    )


def _all_persisted_text(db_path: Path) -> str:
    """Serialize every user-table cell so a leak anywhere in the DB is caught."""
    with sqlite3.connect(db_path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        rows = {
            table: conn.execute(f'SELECT * FROM "{table}"').fetchall()  # noqa: S608 - names from sqlite_master
            for table in tables
        }
    return json.dumps(rows, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# BLOCKER 1 — provider authorization is fail-closed
# ---------------------------------------------------------------------------


def test_codex_refused_for_project_without_authorization(fake_codex, git_repo, tmp_path):
    """`fake_codex` authorizes only AIOS. A different project must not be able
    to launch Codex, and nothing may be persisted before the refusal."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    sup = supervisor.Supervisor()
    with pytest.raises(project_config.ProviderAuthorizationError):
        _start_codex(sup, canonical=git_repo, worktree=worktree, project="AICC")
    assert db.list_runs(sup.db_path) == []


def test_codex_default_policy_is_claude_only():
    """Absent any explicit allow-list, a project authorizes Claude only —
    Codex is present in the registry but never implicitly authorized."""
    assert project_config.allowed_execution_providers("PERSONAL") == ("claude_code",)
    with pytest.raises(project_config.ProviderAuthorizationError):
        project_config.require_execution_provider_allowed("PERSONAL", "codex")


def test_malformed_allow_list_disables_all_execution(fake_codex, git_repo, tmp_path):
    """A corrupt `allowed_agents` policy must disable execution, not silently
    broaden it. This is what an attacker who can only scribble junk into the
    config file gets: nothing runs."""
    storage.atomic_write_json(project_config.CONFIG_FILE, {"AIOS": {"allowed_agents": "codex"}})
    with pytest.raises(project_config.ProviderAuthorizationError, match="malformed"):
        project_config.allowed_execution_providers("AIOS")
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    sup = supervisor.Supervisor()
    with pytest.raises(project_config.ProviderAuthorizationError):
        _start_codex(sup, canonical=git_repo, worktree=worktree)
    assert db.list_runs(sup.db_path) == []


def test_unknown_provider_in_policy_fails_closed():
    storage.atomic_write_json(
        project_config.CONFIG_FILE, {"AIOS": {"allowed_agents": ["claude_code", "rogue_agent"]}}
    )
    with pytest.raises(project_config.ProviderAuthorizationError, match="unknown"):
        project_config.allowed_execution_providers("AIOS")


def test_save_allowed_agents_rejects_unknown_and_empty():
    with pytest.raises(project_config.ProviderAuthorizationError):
        project_config.save_allowed_agents("AIOS", [])
    with pytest.raises(project_config.ProviderAuthorizationError):
        project_config.save_allowed_agents("AIOS", ["codex", "totally_made_up"])


# ---------------------------------------------------------------------------
# BLOCKER 2 — no prompt-derived or credential material reaches persistence
# ---------------------------------------------------------------------------


def test_codex_prompt_echoed_on_stdout_is_redacted_from_persistence(fake_codex, git_repo, tmp_path, monkeypatch):
    """A hostile/echoing provider reflects the operator prompt back onto
    stdout. A distinctive passphrase from the prompt (not matching any generic
    credential regex) must still be scrubbed from every persisted cell, while
    the run itself still completes."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    secret = "MOONBASE-ALPHA-launch-code-99177"
    prompt = f"Deploy using passphrase {secret} immediately."
    monkeypatch.setenv("FAKE_CODEX_ECHO_PROMPT", "1")
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree, prompt=prompt)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED", final["failure_reason"]

    persisted = _all_persisted_text(sup.db_path)
    assert secret not in persisted
    assert prompt not in persisted
    assert "[REDACTED]" in persisted
    # The provider still produced a recognized assistant message + result.
    events = db.list_run_events(sup.db_path, run["id"])
    assert any(event["event_type"] == "assistant_message" for event in events)


def test_codex_audit_metadata_carries_only_prompt_hash(fake_codex, git_repo):
    secret = "GAMMA-secret-passcode-55-omega"
    spec = providers.get_provider("codex").build_launch(
        repository_path=git_repo,
        session_id="unused",
        prompt=f"remember {secret}",
        task_type="review",
        is_resume=False,
        model=None,
    )
    audit_text = providers.audit_json(spec.audit_metadata)
    assert secret not in audit_text
    assert spec.audit_metadata["prompt_transport"] == "stdin"
    assert len(spec.audit_metadata["prompt_sha256"]) == 64


# ---------------------------------------------------------------------------
# SEC-D-01 — the provenance gate must not be bypassable via the Codex executor
# ---------------------------------------------------------------------------


def _codex_sandbox(monkeypatch, git_repo, *, task_type, untrusted=False, operator_elevated=False):
    """The `--sandbox` a Codex launch would use. Availability is stubbed so the
    assertion targets the sandbox-derivation logic itself, independent of any
    real/fake codex binary probe."""
    codex = providers.get_provider("codex")
    stub = providers.ProviderAvailability(
        codex.id, True, "usable", "stub", executable=str(git_repo / "codex"), version="0.0"
    )
    monkeypatch.setattr(codex, "availability", lambda: stub)
    spec = codex.build_launch(
        repository_path=git_repo,
        session_id="unused",
        prompt="do something",
        task_type=task_type,
        is_resume=False,
        model=None,
        untrusted=untrusted,
        operator_elevated=operator_elevated,
    )
    argv = list(spec.argv)
    argv_sandbox = argv[argv.index("--sandbox") + 1]
    assert spec.audit_metadata["sandbox"] == argv_sandbox
    return argv_sandbox


def test_codex_untrusted_implementation_downgrades_to_read_only_sandbox(monkeypatch, git_repo):
    """An untrusted (imported) non-read-only task launched via Codex must run in
    the read-only sandbox — exactly as the Claude path downgrades it. Otherwise a
    prompt-injected imported task obtains `--sandbox workspace-write` (command
    execution + worktree writes) simply by being run through the Codex executor,
    re-opening the SEC-1 / D7 local-RCE class the provenance gate closes."""
    assert (
        _codex_sandbox(monkeypatch, git_repo, task_type="implementation", untrusted=True)
        == "read-only"
    )


def test_codex_operator_elevated_untrusted_task_keeps_workspace_write(monkeypatch, git_repo):
    """An explicit operator elevation is the only thing that restores write
    capability for an untrusted task — matching `agent_runner.profile_for_task`."""
    assert (
        _codex_sandbox(
            monkeypatch,
            git_repo,
            task_type="implementation",
            untrusted=True,
            operator_elevated=True,
        )
        == "workspace-write"
    )


def test_codex_trusted_implementation_keeps_workspace_write(monkeypatch, git_repo):
    """A trusted (operator-authored, non-imported) implementation task is
    unchanged: it still gets the write sandbox it needs to do real work."""
    assert _codex_sandbox(monkeypatch, git_repo, task_type="implementation") == "workspace-write"


def test_codex_prompt_never_enters_task_prompt_history(fake_codex):
    """`launch_service` must not copy a Codex outbound prompt into task
    persistence / prompt history (unlike Claude). `fake_codex` authorizes AIOS
    for Codex so execution reaches the prompt-history branch (rather than
    failing closed at the authorization gate first)."""
    from command_center import launch_service, models

    calls: list = []
    original = models.push_prompt_history
    try:
        models.push_prompt_history = lambda task, prompt: calls.append((task, prompt))
        task = {"id": "t1", "title": "x", "prompt_history": []}
        # Reach the provider launch and let it fail *after* the history branch
        # (the `None` API raises at `start_run`), proving the branch was
        # actually executed — and that it skipped history for Codex.
        with pytest.raises(AttributeError):
            launch_service.execute_agent_launch_v2(
                project="AIOS",
                task_type="review",
                prompt="secret codex prompt",
                timeout_seconds=1,
                repository_path="/nonexistent/worktree",
                execution_center_api=None,
                confirmed=True,
                executor_id="codex",
                task=task,
            )
    finally:
        models.push_prompt_history = original
    assert calls == [], "Codex prompt must never be pushed into prompt history"


# ---------------------------------------------------------------------------
# MAJOR 3 — exit code 0 is not trusted as success
# ---------------------------------------------------------------------------


def test_codex_exit_zero_without_result_event_is_incomplete(fake_codex, git_repo, tmp_path, monkeypatch):
    """Provider exits 0 after a handshake but never emits turn.completed."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "fake"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "working"}}),
    ]
    monkeypatch.setenv("FAKE_CODEX_LINES", json.dumps(lines))
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "incomplete:provider_result_missing"


def test_codex_result_without_assistant_message_is_incomplete(fake_codex, git_repo, tmp_path, monkeypatch):
    """turn.completed with no preceding agent_message is not a valid result."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "fake"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    monkeypatch.setenv("FAKE_CODEX_LINES", json.dumps(lines))
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "incomplete:provider_result_missing"


def test_codex_exit_zero_without_handshake_is_incomplete(fake_codex, git_repo, tmp_path, monkeypatch):
    """Output that never includes a recognized lifecycle handshake is not a
    completed run, even with a well-formed result line."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    lines = [
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    monkeypatch.setenv("FAKE_CODEX_LINES", json.dumps(lines))
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "incomplete:provider_handshake_missing"


# ---------------------------------------------------------------------------
# MAJOR 4 — process identity must be pinned before a run is trusted
# ---------------------------------------------------------------------------


def test_codex_launch_fails_closed_when_identity_unavailable(fake_codex, git_repo, tmp_path, monkeypatch):
    """If a stable process identity cannot be captured, the just-spawned child
    is terminated and the run fails — never left RUNNING and unverifiable."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    monkeypatch.setattr(supervisor, "_capture_stable_process_identity", lambda *a, **k: None)
    sup = supervisor.Supervisor()
    # Signalled by exception rather than a returned FAILED run: `start_raw`'s
    # callers treat any returned run as a started attempt (`launch_ready` would
    # record the queue entry as LAUNCHED). The safety properties this test
    # exists for are unchanged — the child is terminated, nothing is left
    # RUNNING, and the row is terminal with an auditable launch failure.
    with pytest.raises(supervisor.SupervisorError, match="capture process identity"):
        _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = db.list_runs(sup.db_path)[0]
    assert final["state"] == "FAILED"
    assert final["failure_reason"] == "launch_setup_failed"
    assert db.list_runs(sup.db_path, state="RUNNING") == []
    events = db.list_run_events(sup.db_path, final["id"])
    assert any(event["payload"].get("lifecycle") == "launch_failed" for event in events)


# ---------------------------------------------------------------------------
# MAJOR 6 — target worktree safety (symlink + clean-tree)
# ---------------------------------------------------------------------------


def test_codex_symlinked_worktree_is_rejected(fake_codex, git_repo, tmp_path):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    link = tmp_path / "worktree-link"
    link.symlink_to(worktree)
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.SupervisorError, match="symlink"):
        _start_codex(sup, canonical=git_repo, worktree=link)
    assert db.list_runs(sup.db_path) == []


def test_codex_dirty_worktree_is_rejected(fake_codex, git_repo, tmp_path):
    """Codex must start from a clean tree so its own diff is unambiguous."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    (worktree / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.SupervisorError, match="Unsafe Codex target worktree"):
        _start_codex(sup, canonical=git_repo, worktree=worktree)
    assert db.list_runs(sup.db_path) == []


# ---------------------------------------------------------------------------
# MINOR 7 / MAJOR 5 — bounded prompt and bounded persisted events
# ---------------------------------------------------------------------------


def test_codex_oversized_prompt_is_rejected_before_launch(fake_codex, git_repo, tmp_path):
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    huge = "x" * (providers.MAX_CODEX_PROMPT_CHARS + 1)
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.ProviderUnavailableError, match="safety limit"):
        _start_codex(sup, canonical=git_repo, worktree=worktree, prompt=huge)
    assert db.list_runs(sup.db_path) == []


def test_codex_oversized_stdout_line_is_bounded_not_crashing(fake_codex, git_repo, tmp_path, monkeypatch):
    """A single pathologically long stdout line is truncated into a bounded
    malformed event rather than persisted whole or crashing the reader."""
    worktree = _add_worktree(git_repo, tmp_path / "worktree")
    # Exceed the persistence bound without overflowing Linux's single env-string
    # limit (MAX_ARG_STRLEN, 128 KB): the lines reach fake_codex via the
    # FAKE_CODEX_LINES env var, so the oversized payload must stay under that
    # ceiling to remain exec-able on the CI runner while still proving truncation.
    giant = "A" * (providers.MAX_PERSISTED_EVENT_CHARS + 4096)
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "fake"}),
        json.dumps({"type": "turn.started"}),
        giant,  # not JSON, and far over the persistence bound
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    monkeypatch.setenv("FAKE_CODEX_LINES", json.dumps(lines))
    sup = supervisor.Supervisor()
    run = _start_codex(sup, canonical=git_repo, worktree=worktree)
    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED", final["failure_reason"]
    events = db.list_run_events(sup.db_path, run["id"])
    for event in events:
        assert len(json.dumps(event["payload"], ensure_ascii=False)) <= providers.MAX_PERSISTED_EVENT_CHARS + 512


# ---------------------------------------------------------------------------
# Robustness — background persistence tolerates a concurrently deleted run
# ---------------------------------------------------------------------------


def test_stream_event_persistence_tolerates_deleted_run(tmp_path):
    """A stdout/stderr reader thread outlives the launcher; if its run row is
    deleted while output still drains, the append hits a FOREIGN KEY error.
    That benign race must not raise out of the reader thread."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    sup = supervisor.Supervisor(db_path)
    # No such run row exists -> append_run_event raises IntegrityError, which
    # the hardened helper swallows instead of letting the thread die.
    sup._append_stream_event("ghost-run-id", "stdout_line", {"line": "late output"})
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM run_event WHERE run_id = ?", ("ghost-run-id",)).fetchone()[0]
    assert count == 0
