#!/usr/bin/env python3
"""Deterministic Codex CLI test double. Never contacts a provider service."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path


HELP = """Run Codex non-interactively
Usage: codex exec [OPTIONS] [PROMPT]
  instructions are read from stdin
      --json
  -C, --cd <DIR>
"""


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print("codex-cli 0.145.0-fake")
        return 0
    if sys.argv[1:] == ["exec", "--help"]:
        print(HELP)
        return 0
    if len(sys.argv) < 2 or sys.argv[1] != "exec":
        print("unsupported fake interface", file=sys.stderr)
        return 2

    if os.environ.get("FAKE_CODEX_IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, lambda _signum, _frame: None)

    prompt = sys.stdin.read()
    capture = os.environ.get("FAKE_CODEX_PROMPT_CAPTURE")
    if capture:
        Path(capture).write_text(prompt, encoding="utf-8")

    initial_delay = float(os.environ.get("FAKE_CODEX_INITIAL_DELAY", "0"))
    if initial_delay:
        time.sleep(initial_delay)

    scenario = os.environ.get("FAKE_CODEX_SCENARIO", "success")
    if scenario == "startup_failure":
        print("failed during startup", file=sys.stderr, flush=True)
        return 3
    if scenario == "quota":
        print("usage limit reached; check your quota", file=sys.stderr, flush=True)
        return 4
    if scenario == "auth":
        print("not logged in; authentication required", file=sys.stderr, flush=True)
        return 5

    default_lines = [
        json.dumps({"type": "thread.started", "thread_id": "fake-thread"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ]
    lines = json.loads(os.environ.get("FAKE_CODEX_LINES", json.dumps(default_lines)))
    if scenario == "malformed":
        lines.insert(1, "not-json{{{")
    delay = float(os.environ.get("FAKE_CODEX_DELAY", "0.02"))
    for line in lines:
        print(line, flush=True)
        time.sleep(delay)

    stderr = os.environ.get("FAKE_CODEX_STDERR")
    if stderr:
        print(stderr, file=sys.stderr, flush=True)
    touch = os.environ.get("FAKE_CODEX_TOUCH_FILE")
    if touch:
        Path(touch).write_text("modified by fake codex\n", encoding="utf-8")
    extra_sleep = float(os.environ.get("FAKE_CODEX_EXTRA_SLEEP", "0"))
    if extra_sleep:
        time.sleep(extra_sleep)
    return int(os.environ.get("FAKE_CODEX_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
