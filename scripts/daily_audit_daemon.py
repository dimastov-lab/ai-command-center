#!/usr/bin/env python3
"""Long-lived headless host for the daily Command Center self-audit."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from command_center.daily_audit import DailyAuditConfig, DailyAuditService  # noqa: E402
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
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    args = parser.parse_args()

    config = DailyAuditConfig.from_environment(ROOT)
    service = DailyAuditService(config, ExecutionCenterCampaignBackend())
    if args.status:
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
