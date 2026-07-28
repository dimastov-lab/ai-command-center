#!/usr/bin/env python3
"""Long-lived headless host for the daily Command Center self-audit."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from command_center.daily_audit import DailyAuditConfig, DailyAuditService  # noqa: E402
from command_center.daily_audit_backend import ExecutionCenterCampaignBackend  # noqa: E402


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
    while True:
        service.tick()
        if args.once:
            return 0
        time.sleep(max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
