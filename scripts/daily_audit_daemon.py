#!/usr/bin/env python3
"""Long-lived headless host for the daily Command Center self-audit."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from command_center.daily_audit import (  # noqa: E402
    CampaignRequest,
    DailyAuditConfig,
    DailyAuditService,
)
from command_center.daily_audit_backend import ExecutionCenterCampaignBackend  # noqa: E402


def _request_shutdown(
    service: DailyAuditService,
    stop_event: threading.Event,
    _signum: int | None = None,
    _frame: object | None = None,
) -> None:
    """Signal-safe coordination: wake the loop and abort active bounded work."""
    stop_event.set()
    service.request_stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one scheduler tick and exit.")
    parser.add_argument("--status", action="store_true", help="Print persisted scheduler status.")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Check provider authentication and Git remote route without starting a campaign.",
    )
    parser.add_argument(
        "--reset-circuit",
        action="store_true",
        help="Explicitly re-arm an idle scheduler for its next normal interval.",
    )
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    args = parser.parse_args()

    config = DailyAuditConfig.from_environment(ROOT)
    if args.preflight:
        probe = CampaignRequest(
            campaign_id="preflight",
            repository_path=config.repository_path,
            max_remediation_rounds=1,
            merge_mode=config.merge_mode,
            validation_commands=(),
            run_timeout_seconds=config.run_timeout_seconds,
            git_timeout_seconds=config.git_timeout_seconds,
            provider_id=config.provider_id,
            transport_retry_attempts=config.transport_retry_attempts,
            transport_retry_base_seconds=config.transport_retry_base_seconds,
        )
        print(
            json.dumps(
                ExecutionCenterCampaignBackend().preflight(probe),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    service = DailyAuditService(config, ExecutionCenterCampaignBackend())
    if args.status:
        print(service.status_json())
        return 0
    if args.reset_circuit:
        if not service.reset_circuit():
            print("Cannot reset circuit while a campaign is active.", file=sys.stderr)
            return 1
        print(service.status_json())
        return 0
    stop_event = threading.Event()
    previous_handlers = {
        watched: signal.getsignal(watched)
        for watched in (signal.SIGTERM, signal.SIGINT)
    }
    for watched in previous_handlers:
        signal.signal(
            watched,
            lambda signum, frame: _request_shutdown(service, stop_event, signum, frame),
        )
    try:
        while not stop_event.is_set():
            service.tick()
            if args.once:
                return 0
            # Event.wait wakes immediately on SIGTERM/SIGINT; unlike sleep it
            # does not add a full polling interval to shutdown latency.
            stop_event.wait(max(1.0, args.poll_seconds))
        return 0
    finally:
        service.request_stop()
        for watched, previous in previous_handlers.items():
            signal.signal(watched, previous)


if __name__ == "__main__":
    raise SystemExit(main())
