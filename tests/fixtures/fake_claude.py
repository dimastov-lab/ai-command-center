#!/usr/bin/env python3
"""A stand-in for the `claude` binary, used by supervisor/cancellation/
reconciliation tests so they exercise a *real* `subprocess.Popen` (real pid,
real process group, real signal delivery) without ever invoking the actual
`claude` CLI or spending API credits.

Controlled entirely by environment variables so tests can shape its behavior
without needing a new script per scenario:

- `FAKE_CLAUDE_LINES`: JSON list of stdout lines to print, one per line, each
  already a JSON string (so a test can also ask for a deliberately malformed
  line by including plain non-JSON text here). Defaults to a small realistic
  stream-json sequence.
- `FAKE_CLAUDE_INITIAL_DELAY`: seconds to sleep *before* emitting the first
  line (default 0) — simulates a spawned, alive process that produces no early
  stdout (a late handshake). Distinct from `FAKE_CLAUDE_DELAY` (between lines)
  and `FAKE_CLAUDE_EXTRA_SLEEP` (after the last line).
- `FAKE_CLAUDE_DELAY`: seconds to sleep between lines (default 0.05).
- `FAKE_CLAUDE_EXIT_CODE`: process exit code (default 0).
- `FAKE_CLAUDE_STDERR`: a line to print to stderr before exiting, if set.
- `FAKE_CLAUDE_IGNORE_SIGTERM`: if "1", installs a SIGTERM handler that does
  nothing, so a test can exercise the grace-period -> SIGKILL escalation.
- `FAKE_CLAUDE_EXTRA_SLEEP`: extra seconds to sleep after emitting all lines,
  before exiting (default 0) — gives a test time to cancel mid-flight.
- `FAKE_CLAUDE_TOUCH_FILE`: if set, a file path (relative to cwd) to write to
  right after emitting all lines — simulates the working tree changing during
  a run, for cancellation "working tree changed" tests.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

DEFAULT_LINES = [
    json.dumps({"type": "system", "subtype": "init"}),
    json.dumps({"type": "stream_event", "event": {"delta": "hello "}}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello world"}]}}),
    json.dumps({"type": "result", "result": "Verdict: APPROVED_FOR_COMMIT"}),
]


def main() -> int:
    if os.environ.get("FAKE_CLAUDE_IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, lambda signum, frame: None)

    lines_env = os.environ.get("FAKE_CLAUDE_LINES")
    lines = json.loads(lines_env) if lines_env else DEFAULT_LINES
    delay = float(os.environ.get("FAKE_CLAUDE_DELAY", "0.05"))

    initial_delay = float(os.environ.get("FAKE_CLAUDE_INITIAL_DELAY", "0"))
    if initial_delay:
        time.sleep(initial_delay)

    for line in lines:
        print(line)
        sys.stdout.flush()
        time.sleep(delay)

    stderr_line = os.environ.get("FAKE_CLAUDE_STDERR")
    if stderr_line:
        print(stderr_line, file=sys.stderr)
        sys.stderr.flush()

    touch_file = os.environ.get("FAKE_CLAUDE_TOUCH_FILE")
    if touch_file:
        with open(touch_file, "a") as handle:
            handle.write("modified by fake_claude\n")

    extra_sleep = float(os.environ.get("FAKE_CLAUDE_EXTRA_SLEEP", "0"))
    if extra_sleep:
        time.sleep(extra_sleep)

    return int(os.environ.get("FAKE_CLAUDE_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
