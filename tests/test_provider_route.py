from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from command_center import provider_route, run_lineage
from command_center.runtime import db


def _workspace_evidence(*, changed: bool = False) -> provider_route.WorkspaceEvidence:
    return provider_route.WorkspaceEvidence(
        before_head="a" * 40,
        after_head="b" * 40 if changed else "a" * 40,
        before_tree="c" * 40,
        after_tree="d" * 40 if changed else "c" * 40,
    )


def _run(db_path: Path, route: provider_route.ProviderRoute) -> dict:
    task = db.create_task(
        db_path,
        project="AICC",
        title="provider route",
        task_type="implementation",
    )
    session = db.create_session(
        db_path,
        task_id=task["id"],
        project="AICC",
        repository_path="/worktrees/aicc/provider-route",
    )
    return db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AICC",
        task_type="implementation",
        repository_path="/worktrees/aicc/provider-route",
        prompt="route",
        is_resume=False,
        provider_id=route.providers[0],
        provider_route=route.providers,
        max_provider_attempts=route.max_attempts,
        provider_route_reason=route.selection_reason,
        provider_policy_version=route.policy_version,
    )


def test_transient_failure_advances_explicit_route_and_projects_attempts(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    route = provider_route.ProviderRoute(("claude_code", "codex"), max_attempts=2)
    run = _run(db_path, route)

    def operation(provider_id: str, attempt_number: int) -> str:
        if attempt_number == 1:
            raise provider_route.ProviderFailure(
                "provider_api_error",
                provider_route.TRANSIENT,
                workspace_evidence=_workspace_evidence(),
            )
        return f"accepted:{provider_id}"

    result = provider_route.execute_for_run(db_path, run["id"], route, operation)

    assert result.value == "accepted:codex"
    assert [attempt.provider_id for attempt in result.attempts] == [
        "claude_code",
        "codex",
    ]
    assert [attempt.outcome for attempt in result.attempts] == ["failed", "succeeded"]
    view = run_lineage.get_view(db_path, run["id"])
    assert view["provider_route"] == {
        "providers": ["claude_code", "codex"],
        "max_attempts": 2,
        "selection_reason": "explicit_request",
        "policy_version": "project_policy_v1",
    }
    assert [item["attempt_number"] for item in view["provider_attempts"]] == [1, 2]
    assert view["provider_attempts"][0]["classification"] == provider_route.TRANSIENT
    assert "provider_attempts" not in view["unknown_fields"]


@pytest.mark.parametrize(
    "code,classification",
    [
        ("authentication_failed", provider_route.AUTHENTICATION),
        ("policy_denied", provider_route.POLICY),
        ("invalid_request", provider_route.INVALID_REQUEST),
    ],
)
def test_non_retryable_failure_is_fail_fast(tmp_path, code, classification):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    route = provider_route.ProviderRoute(("claude_code", "codex"), max_attempts=2)
    run = _run(db_path, route)

    def operation(_provider_id: str, _attempt_number: int):
        raise provider_route.ProviderFailure(code, classification)

    with pytest.raises(provider_route.RouteExecutionFailed) as excinfo:
        provider_route.execute_for_run(db_path, run["id"], route, operation)

    assert excinfo.value.error_code == code
    assert len(excinfo.value.attempts) == 1
    attempts = db.list_provider_attempts(db_path, run["id"])
    assert len(attempts) == 1
    assert attempts[0]["classification"] == classification


def test_transient_failures_stop_at_configured_bound(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    route = provider_route.ProviderRoute(("claude_code", "codex"), max_attempts=2)
    run = _run(db_path, route)

    def operation(_provider_id: str, _attempt_number: int):
        raise provider_route.ProviderFailure(
            "network_error",
            provider_route.TRANSIENT,
            workspace_evidence=_workspace_evidence(),
        )

    with pytest.raises(provider_route.RouteExecutionFailed) as excinfo:
        provider_route.execute_for_run(db_path, run["id"], route, operation)

    assert excinfo.value.exhausted is True
    assert len(excinfo.value.attempts) == 2
    assert [item["provider_id"] for item in db.list_provider_attempts(db_path, run["id"])] == [
        "claude_code",
        "codex",
    ]


def test_route_rejects_empty_or_unrepresentable_attempt_bound():
    with pytest.raises(ValueError):
        provider_route.ProviderRoute((), max_attempts=1)
    with pytest.raises(ValueError):
        provider_route.ProviderRoute(("claude_code",), max_attempts=2)
    with pytest.raises(ValueError):
        provider_route.ProviderRoute(("claude_code", "claude_code"), max_attempts=2)


def test_policy_filters_before_preference_and_rejects_unauthorized_explicit_provider():
    route = provider_route.ProviderRoute.from_policy(
        allowed_providers=("claude_code", "codex"),
        preferred_provider="codex",
        policy_version="policy-7",
    )
    assert route.providers == ("codex", "claude_code")
    assert route.policy_version == "policy-7"

    with pytest.raises(provider_route.UnauthorizedProviderError):
        provider_route.ProviderRoute.from_policy(
            allowed_providers=("claude_code",),
            preferred_provider="codex",
            policy_version="policy-7",
            explicit_providers=("codex",),
        )


@pytest.mark.parametrize(
    "code,classification,workspace_changed",
    [
        ("cancelled", provider_route.CANCELLED, False),
        ("timeout", provider_route.TIMEOUT, False),
        ("network_error", provider_route.TRANSIENT, True),
        ("incomplete:provider_handshake_missing", provider_route.INCOMPLETE, False),
        ("provider_exit_nonzero", provider_route.UNKNOWN, False),
    ],
)
def test_ambiguous_or_changed_workspace_failures_never_advance_route(
    tmp_path, code, classification, workspace_changed
):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    route = provider_route.ProviderRoute(("claude_code", "codex"), max_attempts=2)
    run = _run(db_path, route)

    def operation(_provider_id: str, _attempt_number: int):
        raise provider_route.ProviderFailure(
            code,
            classification,
            workspace_evidence=_workspace_evidence(changed=workspace_changed),
        )

    with pytest.raises(provider_route.RouteExecutionFailed) as excinfo:
        provider_route.execute_for_run(db_path, run["id"], route, operation)

    assert len(excinfo.value.attempts) == 1
    assert db.list_provider_attempts(db_path, run["id"])[0]["disposition"] == "terminal"


def test_unknown_generic_exception_is_terminal_and_contains_no_secret_text(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    route = provider_route.ProviderRoute(("claude_code", "codex"), max_attempts=2)
    run = _run(db_path, route)

    def operation(_provider_id: str, _attempt_number: int):
        raise RuntimeError("secret-token-value")

    with pytest.raises(provider_route.RouteExecutionFailed):
        provider_route.execute_for_run(db_path, run["id"], route, operation)

    stored = db.list_provider_attempts(db_path, run["id"])
    assert stored[0]["error_code"] == "unexpected_provider_error"
    assert "secret-token-value" not in json.dumps(stored)


def test_attempt_evidence_replay_is_idempotent_but_conflicts_are_rejected(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    route = provider_route.ProviderRoute(("claude_code",), max_attempts=1)
    run = _run(db_path, route)
    started = {
        "run_id": run["id"],
        "attempt_number": 1,
        "provider_id": "claude_code",
        "started_at": "2026-08-08T20:00:00",
    }
    assert db.start_provider_attempt(db_path, **started) == db.start_provider_attempt(
        db_path, **started
    )
    terminal = {
        "run_id": run["id"],
        "attempt_number": 1,
        "outcome": "failed",
        "classification": provider_route.AUTHENTICATION,
        "disposition": provider_route.TERMINAL,
        "error_code": "authentication_failed",
        "completed_at": "2026-08-08T20:00:01",
    }
    assert db.finish_provider_attempt(db_path, **terminal) == db.finish_provider_attempt(
        db_path, **terminal
    )
    with pytest.raises(ValueError, match="immutable"):
        db.finish_provider_attempt(db_path, **{**terminal, "error_code": "policy_denied"})


def test_manual_acceptance_fixture_proves_both_journeys_without_provider_calls():
    script = Path(__file__).parents[1] / "scripts" / "accept_provider_route.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )

    payload = json.loads(completed.stdout)
    assert payload == {
        "external_provider_calls": 0,
        "non_retryable_fail_fast": {"attempts": 1, "error_code": "authentication_failed"},
        "transient_retry_success": {
            "attempts": 2,
            "providers": ["claude_code", "codex"],
            "value": "fixture-success",
        },
    }
