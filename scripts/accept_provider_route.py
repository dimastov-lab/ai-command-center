#!/usr/bin/env python3
"""Hermetic manual acceptance for the Wave 3 provider-route contract."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from command_center import provider_route  # noqa: E402
from command_center.runtime import db  # noqa: E402


def _run(db_path: Path, route: provider_route.ProviderRoute) -> dict:
    task = db.create_task(
        db_path,
        project="AICC",
        title="manual provider-route acceptance",
        task_type="testing",
    )
    session = db.create_session(
        db_path,
        task_id=task["id"],
        project="AICC",
        repository_path=str(ROOT),
    )
    return db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AICC",
        task_type="testing",
        repository_path=str(ROOT),
        prompt="[hermetic fixture]",
        is_resume=False,
        provider_id=route.providers[0],
        provider_route=route.providers,
        max_provider_attempts=route.max_attempts,
        provider_route_reason=route.selection_reason,
        provider_policy_version=route.policy_version,
    )


def main() -> int:
    route = provider_route.ProviderRoute(
        ("claude_code", "codex"),
        max_attempts=2,
        selection_reason="manual_acceptance_fixture",
        policy_version="fixture-v1",
    )
    with tempfile.TemporaryDirectory(prefix="aicc-provider-route-") as temp_dir:
        db_path = Path(temp_dir) / "runtime.db"
        db.migrate(db_path)

        transient_run = _run(db_path, route)

        def transient_then_success(provider_id: str, attempt_number: int) -> str:
            if attempt_number == 1:
                raise provider_route.ProviderFailure(
                    "provider_api_error",
                    provider_route.TRANSIENT,
                    workspace_evidence=provider_route.WorkspaceEvidence(
                        before_head="a" * 40,
                        after_head="a" * 40,
                        before_tree="b" * 40,
                        after_tree="b" * 40,
                    ),
                )
            assert provider_id == "codex"
            return "fixture-success"

        success = provider_route.execute_for_run(
            db_path, transient_run["id"], route, transient_then_success
        )

        fail_fast_run = _run(db_path, route)

        def authentication_failure(_provider_id: str, _attempt_number: int) -> str:
            raise provider_route.ProviderFailure(
                "authentication_failed",
                provider_route.AUTHENTICATION,
            )

        try:
            provider_route.execute_for_run(
                db_path, fail_fast_run["id"], route, authentication_failure
            )
        except provider_route.RouteExecutionFailed as exc:
            fail_fast = exc
        else:  # pragma: no cover - executable acceptance guard
            raise AssertionError("Authentication fixture unexpectedly retried/succeeded")

    print(
        json.dumps(
            {
                "external_provider_calls": 0,
                "non_retryable_fail_fast": {
                    "attempts": len(fail_fast.attempts),
                    "error_code": fail_fast.error_code,
                },
                "transient_retry_success": {
                    "attempts": len(success.attempts),
                    "providers": [attempt.provider_id for attempt in success.attempts],
                    "value": success.value,
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
