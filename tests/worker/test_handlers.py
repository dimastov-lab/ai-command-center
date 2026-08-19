"""The agent_run bridge (SRV-05 slice 2): payload contract and outcome
discipline, with the runner faked at its module seam -- no subprocess, no
claude binary, no repository on disk unless a test builds one."""

from __future__ import annotations

import threading

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
    return build_handlers()["agent_run"], runs


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
    outcome = run_agent(_payload(), _event())
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
            status="failed", exit_code=1, stdout="partial", stderr="boom",
            duration_seconds=2.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:02+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", failed_run)
    outcome = run_agent(_payload(), _event())
    assert outcome.ok and outcome.result["status"] == "failed"


def test_runner_never_started_is_retryable(handler, monkeypatch) -> None:
    run_agent, _ = handler

    def never_started(**kwargs):
        return agent_runner.RunResult(
            status="failed", exit_code=None, stdout="", stderr="no binary",
            duration_seconds=0.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:00+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", never_started)
    outcome = run_agent(_payload(), _event())
    assert not outcome.ok and outcome.retryable


def test_untrusted_mutating_task_is_refused_not_downgraded(handler) -> None:
    """Audit D7 at the queue boundary: refusal with the reason named, not a
    silent read-only downgrade that half-executes and looks completed."""
    run_agent, runs = handler
    outcome = run_agent(
        _payload(task_type="implementation", untrusted=True), _event()
    )
    assert not outcome.ok and not outcome.retryable
    assert "operator elevation" in outcome.reason
    assert runs == []  # nothing executed


def test_unknown_repository_is_retryable_elsewhere(handler, monkeypatch) -> None:
    run_agent, _ = handler

    def refuse(project_id, path):
        raise agent_runner.RunnerError("repository not registered on this host")

    monkeypatch.setattr(agent_runner, "validate_repository", refuse)
    outcome = run_agent(_payload(), _event())
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
            status="completed", exit_code=0, stdout="x" * 50000, stderr="y" * 50000,
            duration_seconds=1.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:00:01+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", chatty)
    outcome = run_agent(_payload(), _event())
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
            status="timed_out", exit_code=None, stdout="partial work...",
            stderr="", duration_seconds=900.0,
            started_at="2026-08-19T12:00:00+00:00",
            completed_at="2026-08-19T12:15:00+00:00",
        )

    monkeypatch.setattr(agent_runner, "run_claude_code", timed_out)
    outcome = run_agent(_payload(), _event())
    assert outcome.ok and outcome.result["status"] == "timed_out"


def test_every_payload_defect_is_non_retryable() -> None:
    """Per-site pinning: only the version error's retryability was asserted,
    so a site-local `retryable=True` regression would dead-letter-loop bad
    payloads through max_attempts (review finding 2)."""
    defects = [
        _payload(prompt="", project_id=None),      # missing fields
        _payload(timeout_seconds=7200),            # beyond visibility ceiling
        _payload(model=123),                       # wrong type
        _payload(untrusted="yes"),                 # wrong type
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
    outcome = run_agent(_payload(), _event())
    assert not outcome.ok and outcome.retryable
    assert "unavailable" in outcome.reason and runs == []
