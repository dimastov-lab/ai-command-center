"""The agent_run bridge (SRV-05 slice 2): payload contract and outcome
discipline, with the runner faked at its module seam -- no subprocess, no
claude binary, no repository on disk unless a test builds one."""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timedelta, timezone

import pytest

from command_center import agent_runner
from command_center.worker.handlers import build_handlers
from command_center.worker.payloads import parse_agent_run, PayloadError


def _payload(**overrides):
    payload = {
        "v": 1,
        "project_id": "proj",
        "repository_path": "/tmp/repo",
        "prompt": "do the thing",
        "task_type": "review",
        "timeout_seconds": 120,
        "untrusted": False,
    }
    payload.update(overrides)
    return payload


def _event() -> threading.Event:
    return threading.Event()


@pytest.fixture
def handler(monkeypatch, tmp_path):
    """The agent_run handler with every external seam faked to succeed."""
    monkeypatch.setattr(
        agent_runner, "validate_repository", lambda project_id, path: tmp_path
    )
    monkeypatch.setattr(agent_runner, "claude_cli_preflight", lambda: (True, "ok"))
    runs: list[dict] = []

    def fake_run(**kwargs):
        runs.append(kwargs)
        return agent_runner.RunResult(
            status="completed",
            exit_code=0,
            stdout='{"result": "done"}',
            stderr="",
            duration_seconds=1.5,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:01+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", fake_run)
    # The writer-lease gate is a real external seam: it shells out to the
    # lease tool whenever VOYN_LEASE_DSN names an authority, and this host
    # has one. Unset it here so the default fixture stays hermetic -- the
    # gate's own tests opt back in explicitly.
    monkeypatch.delenv("VOYN_LEASE_DSN", raising=False)
    return build_handlers()["agent_run"], runs


@pytest.fixture
def lease_tool(monkeypatch, tmp_path):
    """Install a fake ``voyn-lease`` and point the gate at an authority.

    Mirrors tests/orchestrator/test_publish.py: a shell script on disk, so
    the subprocess boundary itself is exercised rather than stubbed out.
    """

    def install(stdout: str = "[]", exit_code: int = 0):
        binary = tmp_path / "fake-voyn-lease"
        binary.write_text(
            "#!/bin/sh\n"
            f"cat <<'JSON'\n{stdout}\nJSON\n"
            f"exit {exit_code}\n"
        )
        binary.chmod(0o755)
        monkeypatch.setenv("VOYN_LEASE_TOOL", str(binary))
        monkeypatch.setenv("VOYN_LEASE_DSN", "postgresql://authority/present")
        return binary

    return install


def _lease_row(worktree, **overrides) -> str:
    row = {
        "repository_id": "ai-command-center",
        "owner": "claude-worker",
        "host": socket.gethostname(),
        "session_id": "sess-1",
        "worktree": str(worktree),
        "process_pid": 4242,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
    }
    row.update(overrides)
    return json.dumps([row])


# -- payload contract ---------------------------------------------------------


def test_unsupported_version_is_non_retryable() -> None:
    error = parse_agent_run(_payload(v=2))
    assert isinstance(error, PayloadError) and not error.retryable
    assert "v1" in error.reason


def test_missing_fields_are_named() -> None:
    error = parse_agent_run(_payload(prompt="", project_id=None))
    assert isinstance(error, PayloadError)
    assert "project_id" in error.reason and "prompt" in error.reason


def test_timeout_beyond_visibility_ceiling_is_refused() -> None:
    error = parse_agent_run(_payload(timeout_seconds=3601))
    assert isinstance(error, PayloadError) and "3600" in error.reason
    assert isinstance(parse_agent_run(_payload(timeout_seconds=True)), PayloadError)


def test_provenance_defaults_to_untrusted() -> None:
    payload = _payload()
    del payload["untrusted"]
    request = parse_agent_run(payload)
    assert request.untrusted is True


# -- outcome discipline -------------------------------------------------------


def test_a_completed_run_reports_ok_with_bounded_result(handler) -> None:
    run_agent, runs = handler
    outcome = run_agent(_payload(), _event(), 1)
    assert outcome.ok
    assert outcome.result["status"] == "completed"
    assert outcome.result["result_text"] == "done"
    assert runs[0]["task_type"] == "review"


def test_agent_failure_is_still_ok_not_redelivered(handler, monkeypatch) -> None:
    """The agent failing the task is a *result*; redelivering a mutating run
    that already executed would re-apply its side effects."""
    run_agent, _ = handler

    def failed_run(**kwargs):
        return agent_runner.RunResult(
            status="failed",
            exit_code=1,
            stdout="partial",
            stderr="boom",
            duration_seconds=2.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:02+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", failed_run)
    outcome = run_agent(_payload(), _event(), 1)
    assert outcome.ok and outcome.result["status"] == "failed"


def test_runner_never_started_is_retryable(handler, monkeypatch) -> None:
    run_agent, _ = handler

    def never_started(**kwargs):
        return agent_runner.RunResult(
            status="failed",
            exit_code=None,
            stdout="",
            stderr="no binary",
            duration_seconds=0.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:00+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", never_started)
    outcome = run_agent(_payload(), _event(), 1)
    assert not outcome.ok and outcome.retryable


def test_untrusted_mutating_task_is_refused_not_downgraded(handler) -> None:
    """Audit D7 at the queue boundary: refusal with the reason named, not a
    silent read-only downgrade that half-executes and looks completed."""
    run_agent, runs = handler
    outcome = run_agent(_payload(task_type="implementation", untrusted=True), _event())
    assert not outcome.ok and not outcome.retryable
    assert "operator elevation" in outcome.reason
    assert runs == []  # nothing executed


def test_unknown_repository_is_retryable_elsewhere(handler, monkeypatch) -> None:
    run_agent, _ = handler

    def refuse(project_id, path):
        raise agent_runner.RunnerError("repository not registered on this host")

    monkeypatch.setattr(agent_runner, "validate_repository", refuse)
    outcome = run_agent(_payload(), _event(), 1)
    assert not outcome.ok and outcome.retryable


def test_lease_lost_before_execution_refuses_to_start(handler) -> None:
    run_agent, runs = handler
    lost = _event()
    lost.set()
    outcome = run_agent(_payload(), lost)
    assert not outcome.ok and outcome.retryable and runs == []


def test_long_output_travels_as_tails(handler, monkeypatch) -> None:
    run_agent, _ = handler

    def chatty(**kwargs):
        return agent_runner.RunResult(
            status="completed",
            exit_code=0,
            stdout="x" * 50000,
            stderr="y" * 50000,
            duration_seconds=1.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:01+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", chatty)
    outcome = run_agent(_payload(), _event(), 1)
    assert len(outcome.result["stdout_tail"]) == 4000
    assert len(outcome.result["stderr_tail"]) == 4000


def test_timed_out_run_is_ok_not_redelivered(handler, monkeypatch) -> None:
    """A timed-out run may have half-executed its mutations; redelivery
    would re-apply them. It is a *result* (status timed_out), never the
    never-started retryable path — pinned because the mutant
    `if run.exit_code is None:` survived review's mutation pass."""
    run_agent, _ = handler

    def timed_out(**kwargs):
        return agent_runner.RunResult(
            status="timed_out",
            exit_code=None,
            stdout="partial work...",
            stderr="",
            duration_seconds=900.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:15:00+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", timed_out)
    outcome = run_agent(_payload(), _event(), 1)
    assert outcome.ok and outcome.result["status"] == "timed_out"


def test_every_payload_defect_is_non_retryable() -> None:
    """Per-site pinning: only the version error's retryability was asserted,
    so a site-local `retryable=True` regression would dead-letter-loop bad
    payloads through max_attempts (review finding 2)."""
    defects = [
        _payload(prompt="", project_id=None),  # missing fields
        _payload(timeout_seconds=7200),  # beyond visibility ceiling
        _payload(model=123),  # wrong type
        _payload(untrusted="yes"),  # wrong type
    ]
    for payload in defects:
        error = parse_agent_run(payload)
        assert isinstance(error, PayloadError), payload
        assert error.retryable is False, error.reason


def test_cli_unavailable_is_retryable_and_runs_nothing(handler, monkeypatch) -> None:
    run_agent, runs = handler
    monkeypatch.setattr(
        agent_runner, "claude_cli_preflight", lambda: (False, "binary missing")
    )
    outcome = run_agent(_payload(), _event(), 1)
    assert not outcome.ok and outcome.retryable
    assert "unavailable" in outcome.reason and runs == []


# -- the executor cascade (BO-S2a) -------------------------------------------


def _cascade_payload(**overrides):
    payload = _payload()
    payload["cascade"] = [
        {"executor": "claude", "task_type": "review"},
        {"executor": "claude", "task_type": "review", "model": "stronger-model"},
    ]
    payload.update(overrides)
    return payload


def test_cascade_selects_the_link_for_this_delivery(handler) -> None:
    run_agent, runs = handler
    outcome = run_agent(_cascade_payload(), _event(), 1)
    assert outcome.ok
    assert runs[-1]["task_type"] == "review"
    assert runs[-1]["model"] is None
    assert outcome.result["cascade_step"] == 1


def test_cascade_second_attempt_takes_the_second_link(handler) -> None:
    run_agent, runs = handler
    outcome = run_agent(_cascade_payload(), _event(), 2)
    assert outcome.ok
    assert runs[-1]["model"] == "stronger-model"
    assert outcome.result["cascade_step"] == 2


def test_cascade_clamps_at_the_tail(handler) -> None:
    """Past the last link the tail keeps serving until the attempt budget
    (the cascade's own length, set by the planner) dead-letters the item."""
    run_agent, runs = handler
    outcome = run_agent(_cascade_payload(), _event(), 7)
    assert outcome.ok
    assert runs[-1]["model"] == "stronger-model"


def test_unavailable_executor_is_a_routing_signal_not_a_task_error(handler) -> None:
    """BO-S2a decision: a link naming an executor this host cannot run is a
    RETRYABLE refusal — the attempt returns to the pool and the next delivery
    selects the next link. Nothing may have executed."""
    run_agent, runs = handler
    payload = _cascade_payload()
    payload["cascade"][0]["executor"] = "codex"
    outcome = run_agent(payload, _event(), 1)
    assert not outcome.ok and outcome.retryable is True
    assert "executor_unavailable" in outcome.reason and "codex" in outcome.reason
    assert runs == [], "an unavailable executor must not execute anything"
    # The same payload on attempt 2 runs the second (available) link.
    assert run_agent(payload, _event(), 2).ok


def test_malformed_cascade_is_a_non_retryable_payload_defect(handler) -> None:
    run_agent, runs = handler
    for bad in (
        {"cascade": "claude"},
        {"cascade": [{"model": "x"}]},
        {"cascade": [42]},
    ):
        outcome = run_agent(_payload(**bad), _event(), 1)
        assert not outcome.ok and outcome.retryable is False, bad
    assert runs == []


def test_absent_cascade_keeps_the_single_executor_behaviour(handler) -> None:
    run_agent, runs = handler
    outcome = run_agent(_payload(), _event(), 3)
    assert outcome.ok
    assert outcome.result["cascade_step"] is None
    assert runs[-1]["task_type"] == _payload()["task_type"]


# -- machine outcome extraction (BO-S3) ---------------------------------------


def test_result_carries_pr_url_and_labelled_head_sha(handler, monkeypatch) -> None:
    from command_center import agent_runner

    run_agent, _runs = handler
    monkeypatch.setattr(
        agent_runner,
        "extract_result_text",
        lambda stdout: (
            "Opened https://github.com/o/r/pull/42 for review.\nHEAD_SHA: deadbeefcafe"
        ),
    )
    outcome = run_agent(_payload(), _event(), 1)
    assert outcome.ok
    assert outcome.result["pr_url"] == "https://github.com/o/r/pull/42"
    assert outcome.result["head_sha"] == "deadbeefcafe"


def test_a_bare_hex_string_is_not_a_head_sha(handler, monkeypatch) -> None:
    """Only the labelled trailer counts: a transcript is full of object ids,
    and guessing which one is the head is the substring-matching the rules
    forbid. No trailer, no sha — the DONE gate simply holds."""
    from command_center import agent_runner

    run_agent, _runs = handler
    monkeypatch.setattr(
        agent_runner,
        "extract_result_text",
        lambda stdout: (
            "commit 0123456789abcdef0123456789abcdef01234567 pushed, no trailer"
        ),
    )
    outcome = run_agent(_payload(), _event(), 1)
    assert outcome.ok
    assert outcome.result["head_sha"] is None
    assert outcome.result["pr_url"] is None


# -- single-writer dispatch gate (VOYN-OPS-WORKER-DISPATCH-INTO-LEASED-WORKTREE)


def test_mutating_dispatch_into_a_leased_worktree_is_refused(
    handler, lease_tool, tmp_path
) -> None:
    """The defect this task names: the worker dispatched into a checkout
    another writer holds, and the collision only surfaced at commit time --
    by which point the foreign edits were already in the tree."""
    run_agent, runs = handler
    lease_tool(_lease_row(tmp_path))
    outcome = run_agent(_payload(task_type="implementation"), _event())
    assert not outcome.ok and outcome.retryable
    assert "claude-worker" in outcome.reason and str(tmp_path) in outcome.reason
    assert runs == [], "nothing may execute in another writer's checkout"


def test_a_lease_one_level_up_still_covers_the_dispatch(
    handler, lease_tool, tmp_path
) -> None:
    """Routing at a subdirectory of a leased checkout is the same collision,
    so containment -- not path equality -- is what the gate tests."""
    run_agent, runs = handler
    lease_tool(_lease_row(tmp_path.parent))
    outcome = run_agent(_payload(task_type="implementation"), _event())
    assert not outcome.ok and outcome.retryable and runs == []


def test_a_read_only_dispatch_is_not_gated(handler, lease_tool, tmp_path) -> None:
    """Scope, stated rather than assumed: a non-mutating run gets the
    read-only sandbox profile and cannot write into the tree at all, so a
    lease it could not violate must not block it."""
    run_agent, runs = handler
    lease_tool(_lease_row(tmp_path))
    outcome = run_agent(_payload(task_type="review"), _event())
    assert outcome.ok and len(runs) == 1


def test_expired_other_host_and_other_path_leases_do_not_block(
    handler, lease_tool, tmp_path
) -> None:
    """Three ways a row can name this path without holding it."""
    run_agent, runs = handler
    stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    for row in (
        _lease_row(tmp_path, expires_at=stale),
        _lease_row(tmp_path, host="some-other-worker"),
        _lease_row(tmp_path.parent / "a-different-checkout"),
    ):
        runs.clear()
        lease_tool(row)
        outcome = run_agent(_payload(task_type="implementation"), _event())
        assert outcome.ok, f"{row} must not block: {outcome.reason}"
        assert len(runs) == 1


def test_an_unreachable_authority_blocks_rather_than_opens(
    handler, lease_tool, monkeypatch, tmp_path
) -> None:
    """A guard that opens when it cannot see is not a guard. Every failure
    to ask a configured authority -- non-zero exit, unreadable output,
    missing binary -- refuses the dispatch."""
    run_agent, runs = handler

    lease_tool("boom: connection refused", exit_code=1)
    refused = run_agent(_payload(task_type="implementation"), _event())
    assert not refused.ok and refused.retryable and runs == []
    assert "connection refused" in refused.reason

    lease_tool("not json at all")
    unreadable = run_agent(_payload(task_type="implementation"), _event())
    assert not unreadable.ok and unreadable.retryable and runs == []
    assert "unreadable" in unreadable.reason

    lease_tool("[]")
    monkeypatch.setenv("VOYN_LEASE_TOOL", str(tmp_path / "does-not-exist"))
    missing = run_agent(_payload(task_type="implementation"), _event())
    assert not missing.ok and missing.retryable and runs == []
    assert "unreachable" in missing.reason


def test_no_configured_authority_leaves_the_gate_inert(handler, tmp_path) -> None:
    """A host with no lease authority has no lease to violate: VOYN_LEASE_DSN
    is the tool's only DSN source, so without it ``list`` cannot answer and
    blocking every mutating run would strand hosts that never had a lease."""
    run_agent, runs = handler
    outcome = run_agent(_payload(task_type="implementation"), _event())
    assert outcome.ok and len(runs) == 1
