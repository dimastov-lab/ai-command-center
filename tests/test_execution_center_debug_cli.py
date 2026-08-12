"""F1 regression coverage for `scripts/execution_center_debug.py`, exercised
as a genuinely separate OS process (not monkeypatched in-process) — this is
the only way to actually prove a CLI's process-lifetime behavior, since the
whole defect being fixed here was specific to what happens when *this*
Python process exits.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from command_center import project_config
from command_center.runtime import db, identity

ROOT = Path(__file__).resolve().parent.parent
CLI_SCRIPT = ROOT / "scripts" / "execution_center_debug.py"
FAKE_CLAUDE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_claude.py"
FAKE_CLAUDE_TREE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_claude_tree.py"


def _extract_last_json_object(stdout: str) -> dict:
    """`_print()` in the CLI always renders a top-level dict starting with a
    lone `{` line (via `json.dumps(..., indent=2)`) — find the last one."""
    lines = stdout.splitlines()
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] == "{":
            start = i
            break
    assert start is not None, f"no JSON object found in CLI output:\n{stdout}"
    return json.loads("\n".join(lines[start:]))


def _cli_env(**overrides) -> dict:
    """Inherits `AICC_DATA_DIR` from this test process's own env (already isolated by
    `tests/conftest.py`), and additionally sets `AICC_REPORTS_ROOT` — `AICC_DATA_DIR`
    alone is not enough, because `command_center.runtime.reports.REPORTS_ROOT` is not
    derived from it (reports live at `<repo>/reports/`, not under `data/`; see that
    module's comment). Without this, every subprocess launched by this file's tests
    wrote a real report into the developer's actual `reports/AIOS/` directory on every
    `pytest` run — the CLI subprocess re-imports `reports.py` fresh, so the in-process
    `isolated_reports_dir` monkeypatch in conftest.py can never reach it."""
    env = dict(os.environ)
    env["AICC_CLAUDE_BINARY"] = str(FAKE_CLAUDE_SCRIPT)
    if "AICC_DATA_DIR" in env:
        env["AICC_REPORTS_ROOT"] = str(Path(env["AICC_DATA_DIR"]) / "reports")
    # Same default as `tests/conftest.py`'s `fake_claude` fixture: a plain
    # "implementation"-type fake run genuinely touches the working tree, so
    # `runtime.outcome.classify_process_result` classifies it `COMPLETED`
    # rather than `INCOMPLETE` (`REQUIRES_CHANGES_TASK_TYPES` + an unchanged
    # tree). A test exercising the unchanged-tree path overrides this back
    # to `""` via `overrides`.
    env["FAKE_CLAUDE_TOUCH_FILE"] = "fake_claude_default_touch.txt"
    env.update(overrides)
    return env


@pytest.fixture
def configured_repo(git_repo):
    """Points the real (isolated, per-conftest AICC_DATA_DIR) project_config
    at `git_repo` for project AIOS — the CLI subprocess inherits the same
    AICC_DATA_DIR from this test process's environment."""
    project_config.save_repository_path("AIOS", str(git_repo))
    yield git_repo
    project_config.save_repository_path("AIOS", None)


def test_launch_blocks_until_terminal_state_and_persists_final_events(configured_repo):
    """1: blocks until terminal state. 2: final stream/result events are
    persisted before the CLI process exits."""
    env = _cli_env(FAKE_CLAUDE_DELAY="0.3")  # ~4 lines * 0.3s so a premature return would be detectable
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "launch", "AIOS", str(configured_repo), "implementation", "say ok", "--confirm"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    elapsed = time.monotonic() - started

    assert elapsed >= 1.0, "the CLI must block for the real duration of the run, not return right after Popen"
    assert result.returncode == 0, result.stdout + result.stderr

    final = _extract_last_json_object(result.stdout)
    assert final["state"] == "COMPLETED"

    # The final `result` stream-json event must have been persisted — not
    # just the initial `process_started` lifecycle event (the exact bug: an
    # earlier version of this CLI left only that one event behind).
    events = db.list_run_events(db.resolve_db_path(), final["id"])
    event_types = {e["event_type"] for e in events}
    assert "result" in event_types
    assert "process_exited" in {e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"}


def test_no_orphan_process_survives_normal_cli_exit(configured_repo):
    """3: no child (or grandchild) process survives CLI exit — normal
    completion path."""
    env = _cli_env()
    result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "launch", "AIOS", str(configured_repo), "implementation", "say ok", "--confirm"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    final = _extract_last_json_object(result.stdout)
    pid = final["pid"]
    assert pid is not None
    time.sleep(0.3)
    assert identity.process_exists(pid) is False


def test_ctrl_c_triggers_confirmed_cancellation_and_reaches_terminal_state(configured_repo):
    """4: Ctrl+C/explicit foreground cancellation reaches a terminal Run
    state, and does not merely kill the CLI process while abandoning the
    child."""
    env = _cli_env(FAKE_CLAUDE_EXTRA_SLEEP="30")
    proc = subprocess.Popen(
        [sys.executable, str(CLI_SCRIPT), "launch", "AIOS", str(configured_repo), "implementation", "say ok", "--confirm"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        time.sleep(1.0)  # let it launch and enter the foreground polling loop
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    assert proc.returncode != 0, f"Ctrl+C must not report success\nstdout={stdout}\nstderr={stderr}"

    final = _extract_last_json_object(stdout)
    assert final["state"] == "CANCELLED"

    pid = final["pid"]
    assert pid is not None
    time.sleep(0.3)
    assert identity.process_exists(pid) is False, "the underlying claude process must not survive Ctrl+C either"


@pytest.mark.serial  # spawns a real 3-level process tree on a deadline; times out under xdist CPU load
def test_no_orphan_at_any_level_when_cancelled_via_ctrl_c(configured_repo):
    """3+4 combined, using the parent->child->grandchild fixture: Ctrl+C
    during `launch` must clean up the entire process tree, not just the
    top-level process the Supervisor directly `Popen`'d."""
    pidfile_base = str(configured_repo / "tree_pids")
    env = _cli_env()
    env["AICC_CLAUDE_BINARY"] = str(FAKE_CLAUDE_TREE_SCRIPT)
    env["FAKE_CLAUDE_TREE_PIDFILE"] = pidfile_base

    proc = subprocess.Popen(
        [sys.executable, str(CLI_SCRIPT), "launch", "AIOS", str(configured_repo), "implementation", "say ok", "--confirm"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 10
        pids = {}
        while time.monotonic() < deadline:
            if all(os.path.exists(f"{pidfile_base}.{role}") for role in ("parent", "child", "grandchild")):
                for role in ("parent", "child", "grandchild"):
                    pids[role] = int(Path(f"{pidfile_base}.{role}").read_text().strip())
                break
            time.sleep(0.05)
        assert len(pids) == 3, "process tree never came up"

        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    assert proc.returncode != 0

    # The process tree exits asynchronously after SIGINT — the grandchild in
    # particular may still be unwinding when `communicate()` returns. Poll
    # (bounded) for each pid to disappear rather than asserting on a single fixed
    # sleep, which raced under CI load and flaked. A genuinely orphaned process
    # still fails: it simply never disappears before the deadline.
    deadline = time.monotonic() + 10
    for role, pid in pids.items():
        while identity.process_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert identity.process_exists(pid) is False, f"{role} (pid {pid}) must not survive Ctrl+C cancellation"


def test_cli_does_not_advertise_a_cross_invocation_cancel_command():
    """5: the CLI never advertises a cross-invocation cancel operation it
    cannot perform — there is no `cancel` subcommand at all."""
    result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "cancel", "some-run-id", "--confirm"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "invalid choice: 'cancel'" in result.stderr

    help_result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "--help"], capture_output=True, text=True, timeout=10
    )
    # The prose explaining *why* there's no cancel subcommand legitimately
    # mentions the word "cancel" — what must not exist is an actual `{...}`
    # subcommand choice named "cancel" in the usage/subparser listing.
    usage_block = help_result.stdout.split("positional arguments:")[1] if "positional arguments:" in help_result.stdout else help_result.stdout
    subcommand_line = next((line for line in usage_block.splitlines() if "{" in line and "}" in line), "")
    assert "cancel" not in subcommand_line, f"'cancel' must not be a listed subcommand: {subcommand_line!r}"


def test_cli_read_only_subcommands_are_safe_across_separate_invocations(configured_repo):
    """The read-only inspection subcommands, unlike a hypothetical
    cross-process cancel, are genuinely safe to call from a fresh invocation
    — they only read shared SQLite state."""
    env = _cli_env()
    launch_result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "launch", "AIOS", str(configured_repo), "implementation", "say ok", "--confirm"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    final = _extract_last_json_object(launch_result.stdout)

    status_result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "run-status", final["id"]], capture_output=True, text=True, timeout=10,
    )
    assert status_result.returncode == 0
    status = _extract_last_json_object(status_result.stdout)
    assert status["state"] == "COMPLETED"

    events_result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "events", final["id"]], capture_output=True, text=True, timeout=10,
    )
    assert events_result.returncode == 0
    events = json.loads(events_result.stdout)
    assert any(e["event_type"] == "result" for e in events)
