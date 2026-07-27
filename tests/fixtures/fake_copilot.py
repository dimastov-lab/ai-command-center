#!/usr/bin/env python3
"""Deterministic Copilot CLI test double. Never contacts GitHub or any provider service."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path


VERSION = "GitHub Copilot CLI 1.0.75-fake"


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print(VERSION)
        return 0

    if os.environ.get("FAKE_COPILOT_IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, lambda _signum, _frame: None)

    pid_capture = os.environ.get("FAKE_COPILOT_PID_CAPTURE")
    if pid_capture:
        Path(pid_capture).write_text(str(os.getpid()), encoding="utf-8")

    prompt = sys.stdin.read()
    capture = os.environ.get("FAKE_COPILOT_PROMPT_CAPTURE")
    if capture:
        Path(capture).write_text(prompt, encoding="utf-8")

    initial_delay = float(os.environ.get("FAKE_COPILOT_INITIAL_DELAY", "0"))
    if initial_delay:
        time.sleep(initial_delay)

    scenario = os.environ.get("FAKE_COPILOT_SCENARIO", "success")
    if scenario == "startup_failure":
        print("failed during startup", file=sys.stderr, flush=True)
        return 3
    if scenario == "quota":
        print("AI credit usage limit reached", file=sys.stderr, flush=True)
        return 4
    if scenario == "auth":
        print("not logged in; authentication required", file=sys.stderr, flush=True)
        return 5

    agent_text = "done"
    if os.environ.get("FAKE_COPILOT_ECHO_PROMPT") == "1":
        agent_text = f"Understood. Echoing your instructions verbatim: {prompt}"

    default_events = [
        {"type": "session.tools_updated", "data": {"model": "claude-sonnet-5"}},
        {"type": "user.message", "data": {"content": prompt, "transformedContent": prompt}},
        {"type": "assistant.turn_start", "data": {"turnId": "0"}},
        {"type": "assistant.message", "data": {"content": agent_text, "model": "claude-sonnet-5", "toolRequests": []}},
        {"type": "assistant.turn_end", "data": {"turnId": "0"}},
        {"type": "assistant.idle", "data": {}},
        {"type": "session.usage_checkpoint", "data": {"totalNanoAiu": 1000, "totalPremiumRequests": 1}},
        {"type": "result", "exitCode": 0, "usage": {"premiumRequests": 1, "totalApiDurationMs": 100}},
    ]

    events = json.loads(os.environ.get("FAKE_COPILOT_EVENTS", json.dumps(default_events)))
    if scenario == "malformed":
        events.insert(2, "not-json{{{")
    if scenario == "no_turn_end":
        events = [e for e in events if e.get("type") != "assistant.turn_end"]
    if scenario == "no_assistant_message":
        events = [e for e in events if e.get("type") != "assistant.message"]

    delay = float(os.environ.get("FAKE_COPILOT_DELAY", "0.01"))
    for event in events:
        if isinstance(event, str):
            print(event, flush=True)
        else:
            print(json.dumps(event), flush=True)
        time.sleep(delay)

    stderr = os.environ.get("FAKE_COPILOT_STDERR")
    if stderr:
        print(stderr, file=sys.stderr, flush=True)
    touch = os.environ.get("FAKE_COPILOT_TOUCH_FILE")
    if touch:
        Path(touch).write_text("modified by fake copilot\n", encoding="utf-8")
    extra_sleep = float(os.environ.get("FAKE_COPILOT_EXTRA_SLEEP", "0"))
    if extra_sleep:
        time.sleep(extra_sleep)
    return int(os.environ.get("FAKE_COPILOT_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())