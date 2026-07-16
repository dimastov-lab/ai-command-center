"""Session Supervisor: owns the Claude Code subprocess lifecycle.

Normative decisions (frozen for Sprint 1 — see the Sprint 1 brief):

- `claude --session-id <uuid>` for a fresh run, exact-id `claude --resume <uuid>`
  for a resumed one. Never `--continue`/`-c`, never `--background`/`--bg`, never
  `claude agents` as a lifecycle registry — this module's own SQLite `run` table
  *is* the lifecycle registry.
- `--output-format stream-json --include-partial-messages --verbose
  --setting-sources ""` on every launch.
- `subprocess.Popen`, `shell=False`, `start_new_session=True` (so the child
  becomes its own process group leader — required for the SIGTERM-to-process-
  group / SIGKILL-after-grace cancellation below), stdin disconnected
  (`subprocess.DEVNULL`).
- The Supervisor owns: process lifecycle, PID/process-start metadata, stdout/
  stderr consumption, incremental `RunEvent` persistence, cancellation,
  timeout enforcement, and startup reconciliation. Claude owns: reasoning,
  conversation, and provider session state — this module never inspects or
  interprets assistant content beyond classifying it for storage
  (`stream_parser`).

No database transaction here ever spans a subprocess call: `db.create_run`/
`db.update_run_state`/`db.update_run_fields`/`db.append_run_event` each open
and close their own short transaction, and `subprocess.Popen(...)` itself is
never called from inside one.

**This module does not assemble context and does not enforce the BANK/LEGAL
sensitive-project boundary.** `start_raw()` executes whatever `prompt` string
it is given, verbatim — it is the internal, low-level process executor, not
an application-facing entry point. The sensitive-project boundary
(`context_service.assemble_context`) is enforced one layer up, in
`ExecutionCenterAPI.start_run`, which is the only route application code
should use to launch a run against a real project. `start_raw()` is public
(tests call it directly, and a future non-project internal caller reasonably
could), but its name is deliberately not `start()` — nothing about its name
suggests it is safe to call with a caller-assembled prompt for a sensitive
project without having gone through `context_service` first.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

from command_center import agent_runner
from command_center.models import iso_now
from command_center.runtime import context_service, db, identity, reports, stream_parser

# `AICC_CLAUDE_BINARY` lets a test point a genuinely separate OS process
# (e.g. `scripts/execution_center_debug.py`, invoked as a real subprocess, not
# just monkeypatched in-process) at an executable test double instead of the
# real `claude` CLI — the in-process `fake_claude` test fixture cannot do
# this for a *different* process's fresh import of this module. Unset in
# every normal (non-test) invocation, so production behavior is unaffected.
CLAUDE_BINARY = os.environ.get("AICC_CLAUDE_BINARY") or "claude"

# No default timeout is applied *here* — `start_raw`'s `timeout_seconds`
# param means exactly what it says (`None` = no automatic timeout). A
# sensible default policy (e.g. "900s unless the caller opts out") belongs to
# the application layer (`ExecutionCenterAPI.start_run`), not this low-level
# executor.
DEFAULT_CANCEL_GRACE_SECONDS = 10.0

# Defense in depth: `build_claude_command` never constructs these, but every
# command is checked against this set before it is ever handed to `Popen`.
_FORBIDDEN_FLAGS = frozenset({"--continue", "-c", "--background", "--bg"})


class SupervisorError(Exception):
    """Raised for a launch/cancel request that cannot be carried out."""


def build_claude_command(
    *,
    session_id: str,
    prompt: str,
    task_type: str,
    is_resume: bool,
    model: str | None = None,
) -> list[str]:
    """Construct the exact `claude` argv for one run.

    Fresh run: `claude --session-id <uuid> -p <prompt> --output-format
    stream-json --include-partial-messages --verbose --setting-sources ""`.
    Resume: identical, except `--resume <uuid>` (the *exact* id — never a
    bare `--resume` picker, never `--continue`) in place of `--session-id`.
    """
    command = [CLAUDE_BINARY]
    if is_resume:
        command += ["--resume", session_id]
    else:
        command += ["--session-id", session_id]
    command += [
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--setting-sources",
        "",
    ]
    if task_type in agent_runner.READ_ONLY_TASK_TYPES:
        command += ["--tools", ",".join(agent_runner.READ_ONLY_ALLOWED_TOOLS)]
    else:
        command += ["--disallowedTools", ",".join(agent_runner.GIT_WRITE_DISALLOWED_TOOLS)]
    if model:
        command += ["--model", model]
    return command


def _assert_no_forbidden_flags(command: list[str]) -> None:
    hit = _FORBIDDEN_FLAGS.intersection(command)
    if hit:
        raise SupervisorError(f"Forbidden flag(s) constructed into command: {sorted(hit)}")


class _ActiveRun:
    def __init__(self, *, process: subprocess.Popen, run_id: str) -> None:
        self.process = process
        self.run_id = run_id
        self.done_event = threading.Event()
        # Set by `_timeout_watchdog` (never by `cancel()`) so `_supervise` can
        # tell a timeout-triggered termination apart from an explicit,
        # human-confirmed cancellation when it decides the final state.
        self.timeout_triggered = threading.Event()


class Supervisor:
    """One Supervisor instance per running Execution Center backend process.

    Holds an in-memory registry of runs it personally launched and can still
    signal/read from (`self._active`). This registry is intentionally *not*
    the source of truth — the SQLite `run` table is — because it cannot
    survive a Supervisor process restart (a child process's stdout pipe and
    waitable-child relationship both belong to the specific process that
    called `Popen`, not to "the Supervisor" as a concept). See `reconcile()`
    for how a fresh Supervisor instance handles what its predecessor left
    RUNNING.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or db.resolve_db_path()
        db.migrate(self.db_path)
        self._active: dict[str, _ActiveRun] = {}
        self._active_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    def start_raw(
        self,
        *,
        project: str,
        repository_path: str,
        task_type: str,
        prompt: str,
        confirmed: bool,
        task_id: str | None = None,
        title: str | None = None,
        session_id: str | None = None,
        is_resume: bool = False,
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict:
        """Prepare and launch a run from an already-final `prompt` string.

        **Internal/low-level.** This method executes `prompt` verbatim — it
        does not call `context_service.assemble_context` and does not know
        or care whether `project` is sensitive (BANK/LEGAL). Application code
        that launches a run against a real project must go through
        `ExecutionCenterAPI.start_run` instead, which assembles the prompt
        through `context_service` before ever reaching here. This method
        exists for `ExecutionCenterAPI` itself to call (once it has already
        built a safe prompt) and for tests that need to exercise process
        lifecycle mechanics directly without the context-assembly layer.

        `timeout_seconds=None` means no automatic timeout — the run stays
        `RUNNING` until it exits on its own or is explicitly cancelled. Pass
        a number to enable the timeout watchdog (see `_timeout_watchdog`).

        Returns the run row once the subprocess has been started (state
        `RUNNING`), or a row left in state `FAILED` if `Popen` itself could
        not start the process (e.g. the `claude` binary is missing) — this
        method never raises for that specific failure mode, matching
        `agent_runner.run_claude_code`'s existing convention. It *does*
        raise before any subprocess is spawned for: missing confirmation,
        an unconfigured/mismatched repository path, or an invalid resume
        request (no such session).
        """
        context_service.require_launch_confirmation(confirmed, what="Launching a Claude Code run")

        repo_path = agent_runner.validate_repository(project, repository_path)

        if task_id is None:
            task = db.create_task(
                self.db_path, project=project, title=title or prompt[:120], task_type=task_type
            )
            task_id = task["id"]
        elif db.get_task(self.db_path, task_id) is None:
            db.create_task(
                self.db_path,
                project=project,
                title=title or prompt[:120],
                task_type=task_type,
                task_id=task_id,
            )

        if is_resume:
            if not session_id:
                raise SupervisorError("Resuming a session requires an explicit session_id.")
            session = db.get_session(self.db_path, session_id)
            if session is None:
                raise SupervisorError(f"No such session to resume: {session_id!r}")
        else:
            session = db.create_session(
                self.db_path,
                task_id=task_id,
                project=project,
                repository_path=str(repo_path),
                session_id=session_id,
            )
            session_id = session["id"]

        command = build_claude_command(
            session_id=session_id, prompt=prompt, task_type=task_type, is_resume=is_resume, model=model
        )
        _assert_no_forbidden_flags(command)

        run = db.create_run(
            self.db_path,
            session_id=session_id,
            task_id=task_id,
            project=project,
            task_type=task_type,
            repository_path=str(repo_path),
            prompt=prompt,
            is_resume=is_resume,
            timeout_seconds=timeout_seconds,
            command=command,
        )
        run = db.update_run_state(self.db_path, run["id"], expected_version=run["version"], new_state="QUEUED")

        pre_run_status = agent_runner.git_snapshot(repo_path).get("status_summary")
        run = db.update_run_fields(
            self.db_path,
            run["id"],
            expected_version=run["version"],
            fields={"pre_run_git_status": pre_run_status},
        )

        return self._launch_process(run, command, repo_path)

    def _launch_process(self, run: dict, command: list[str], repo_path: Path) -> dict:
        run_id = run["id"]
        try:
            process = subprocess.Popen(
                command,
                cwd=repo_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            run = db.update_run_state(
                self.db_path,
                run_id,
                expected_version=run["version"],
                new_state="FAILED",
                fields={"completed_at": iso_now()},
            )
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event("launch_failed", error=str(exc))["payload"],
            )
            return run

        pid = process.pid
        proc_identity = identity.capture_identity(pid)
        run = db.update_run_fields(
            self.db_path,
            run_id,
            expected_version=run["version"],
            fields={
                "pid": pid,
                "process_start_identity": proc_identity.as_string() if proc_identity else None,
            },
        )
        run = db.update_run_state(
            self.db_path,
            run_id,
            expected_version=run["version"],
            new_state="RUNNING",
            fields={"started_at": iso_now()},
        )
        db.append_run_event(
            self.db_path, run_id, "lifecycle", stream_parser.lifecycle_event("process_started", pid=pid)["payload"]
        )

        active = _ActiveRun(process=process, run_id=run_id)
        with self._active_lock:
            self._active[run_id] = active

        waiter = threading.Thread(target=self._supervise, args=(run_id, active, repo_path), daemon=True)
        waiter.start()

        timeout_seconds = run.get("timeout_seconds")
        if timeout_seconds is not None:
            watchdog = threading.Thread(
                target=self._timeout_watchdog, args=(run_id, active, timeout_seconds), daemon=True
            )
            watchdog.start()

        return db.get_run(self.db_path, run_id)

    # ------------------------------------------------------------------
    # Streaming consumption (runs in background reader threads)
    # ------------------------------------------------------------------

    def _drain_stdout(self, run_id: str, process: subprocess.Popen) -> None:
        try:
            for line in process.stdout:
                event = stream_parser.parse_stream_line(line)
                if event is None:
                    continue
                db.append_run_event(self.db_path, run_id, event["event_type"], event["payload"])
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    def _drain_stderr(self, run_id: str, process: subprocess.Popen) -> None:
        try:
            for line in process.stderr:
                event = stream_parser.stderr_event(line)
                db.append_run_event(self.db_path, run_id, event["event_type"], event["payload"])
        finally:
            try:
                process.stderr.close()
            except Exception:
                pass

    def _supervise(self, run_id: str, active: _ActiveRun, repo_path: Path) -> None:
        process = active.process
        stdout_thread = threading.Thread(target=self._drain_stdout, args=(run_id, process), daemon=True)
        stderr_thread = threading.Thread(target=self._drain_stderr, args=(run_id, process), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        exit_code = process.wait()
        stdout_thread.join()
        stderr_thread.join()

        run = db.get_run(self.db_path, run_id)
        cancel_requested = bool(run.get("cancel_requested"))
        timed_out = active.timeout_triggered.is_set()

        post_status = agent_runner.git_snapshot(repo_path).get("status_summary")
        pre_status = run.get("pre_run_git_status")
        working_tree_changed = pre_status is not None and post_status != pre_status

        # An explicit human cancellation takes precedence in classification
        # over a timeout that may have fired concurrently with it.
        failure_reason = None
        if cancel_requested:
            new_state = "CANCELLED"
        elif timed_out:
            new_state = "FAILED"
            failure_reason = "timeout"
        elif exit_code == 0:
            new_state = "COMPLETED"
        else:
            new_state = "FAILED"

        run = db.update_run_state(
            self.db_path,
            run_id,
            expected_version=run["version"],
            new_state=new_state,
            fields={
                "exit_code": exit_code,
                "completed_at": iso_now(),
                "post_run_git_status": post_status,
                "working_tree_changed": 1 if working_tree_changed else 0,
                "failure_reason": failure_reason,
            },
        )
        db.append_run_event(
            self.db_path,
            run_id,
            "lifecycle",
            stream_parser.lifecycle_event(
                "process_exited",
                exit_code=exit_code,
                state=new_state,
                working_tree_changed=working_tree_changed,
                failure_reason=failure_reason,
            )["payload"],
        )
        if new_state == "CANCELLED" and working_tree_changed:
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event(
                    "cancellation_working_tree_changed_requires_inspection",
                    pre_run_git_status=pre_status,
                    post_run_git_status=post_status,
                )["payload"],
            )

        if new_state in ("COMPLETED", "FAILED", "CANCELLED"):
            events = db.list_run_events(self.db_path, run_id, after_seq=0, limit=1_000_000)
            path = reports.save_report(run, events)
            # Relative to `REPORTS_ROOT`'s *parent* (not the hardcoded repo
            # root), so this stays correct when a test monkeypatches
            # `reports.REPORTS_ROOT` to an isolated directory.
            db.create_report(self.db_path, run_id, str(path.relative_to(reports.REPORTS_ROOT.parent)))

        with self._active_lock:
            self._active.pop(run_id, None)
        active.done_event.set()

    # ------------------------------------------------------------------
    # Cancellation — requires explicit confirmation from the caller/UI
    # ------------------------------------------------------------------

    def cancel(
        self, run_id: str, *, confirmed: bool, grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS
    ) -> dict:
        """SIGTERM to the run's process group, then SIGKILL only after
        `grace_seconds` if it hasn't exited. Never runs `git restore/reset/
        clean` — the working tree is left exactly as the process left it,
        and its post-cancellation status is captured and compared to the
        pre-run snapshot (see `_supervise`'s
        `cancellation_working_tree_changed_requires_inspection` event).
        """
        context_service.require_launch_confirmation(confirmed, what="Cancelling a run")

        with self._active_lock:
            active = self._active.get(run_id)
        if active is None:
            raise SupervisorError(
                f"Run {run_id!r} is not an actively supervised run in this process instance "
                "(already finished, or supervised by a different process instance)."
            )

        run = db.get_run(self.db_path, run_id)
        if run is None:
            raise KeyError(f"No such run: {run_id!r}")
        if run["state"] != "RUNNING":
            raise SupervisorError(f"Run {run_id!r} is not RUNNING (state={run['state']!r}); nothing to cancel.")

        try:
            run = db.update_run_fields(
                self.db_path,
                run_id,
                expected_version=run["version"],
                fields={"cancel_requested": 1, "cancel_requested_at": iso_now()},
            )
        except db.LostUpdateError:
            current = db.get_run(self.db_path, run_id)
            raise SupervisorError(
                f"Run {run_id!r} changed state before cancellation could be recorded "
                f"(now state={current['state']!r})."
            ) from None

        db.append_run_event(
            self.db_path, run_id, "lifecycle", stream_parser.lifecycle_event("cancel_requested")["payload"]
        )

        pid = active.process.pid
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        db.append_run_event(
            self.db_path,
            run_id,
            "lifecycle",
            stream_parser.lifecycle_event("cancel_sigterm_sent", pid=pid)["payload"],
        )

        exited_in_time = active.done_event.wait(timeout=grace_seconds)
        if not exited_in_time:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event("cancel_sigkill_sent", pid=pid)["payload"],
            )
            active.done_event.wait(timeout=grace_seconds)

        return db.get_run(self.db_path, run_id)

    # ------------------------------------------------------------------
    # Timeout watchdog — same SIGTERM -> grace -> SIGKILL mechanism as
    # cancel(), triggered by an elapsed deadline instead of a caller request.
    # ------------------------------------------------------------------

    def _timeout_watchdog(
        self,
        run_id: str,
        active: _ActiveRun,
        timeout_seconds: float,
        grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
    ) -> None:
        """Waits (using a monotonic-backed `threading.Event`, not wall-clock
        polling) up to `timeout_seconds` for the run to finish on its own. If
        it hasn't, terminates it via the identical process-group SIGTERM ->
        grace -> SIGKILL sequence `cancel()` uses, and marks
        `active.timeout_triggered` so `_supervise` records the terminal state
        as `FAILED` with `failure_reason="timeout"` rather than `CANCELLED`
        (which is reserved for an explicit, human-confirmed cancellation).
        Never runs `git restore/reset/clean` — same guarantee as `cancel()`.
        """
        exited_before_deadline = active.done_event.wait(timeout=timeout_seconds)
        if exited_before_deadline:
            return  # finished on its own before the deadline; nothing to do

        active.timeout_triggered.set()
        db.append_run_event(
            self.db_path,
            run_id,
            "lifecycle",
            stream_parser.lifecycle_event("timeout_exceeded", timeout_seconds=timeout_seconds)["payload"],
        )

        pid = active.process.pid
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        db.append_run_event(
            self.db_path,
            run_id,
            "lifecycle",
            stream_parser.lifecycle_event("timeout_sigterm_sent", pid=pid)["payload"],
        )

        exited_in_time = active.done_event.wait(timeout=grace_seconds)
        if not exited_in_time:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event("timeout_sigkill_sent", pid=pid)["payload"],
            )

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    def reconcile(self) -> list[dict]:
        """Inspect every run currently recorded `RUNNING` and classify it
        conservatively. Never signals a process based only on a reused pid,
        and never guesses that a run silently completed. Does not consult
        `claude agents --json` — this SQLite `run` table is the Supervisor's
        own lifecycle registry, entirely independent of the `claude` CLI's
        background-agent registry (which p-mode runs never touch anyway,
        since `--background`/`--bg` is prohibited everywhere in this module).

        Classification:

        - No pid was ever recorded -> `INTERRUPTED` (we never captured what
          to check; the process's fate is simply unrecorded).
        - pid does not currently exist -> `INTERRUPTED` (provably gone; we do
          not know whether it completed or failed before it disappeared, so
          we do not claim `COMPLETED`/`FAILED`).
        - pid exists but no identity was recorded at launch time to compare
          against -> `UNKNOWN` (nothing here proves or disproves it's ours).
        - pid exists but its current identity does not match what was
          recorded at launch -> `INTERRUPTED` (a reused pid now running a
          different process; the original process is gone).
        - pid exists and its identity matches exactly -> left as `RUNNING`,
          but flagged with a `reconciliation_orphaned` event and *not*
          re-registered as an actively supervised run: this Supervisor
          instance has no stdout/stderr pipe or waitable-child handle for a
          process it did not itself `Popen`, so it cannot resume incremental
          persistence and must not attempt to signal/cancel it.
        """
        outcomes = []
        for run in db.list_runs(self.db_path, state="RUNNING"):
            run_id = run["id"]
            pid = run.get("pid")
            recorded_identity = run.get("process_start_identity")

            if pid is None:
                classification = "INTERRUPTED"
                detail = "no pid recorded for this run"
            else:
                current = identity.capture_identity(pid)
                if current is None:
                    classification = "INTERRUPTED"
                    detail = "pid no longer exists"
                elif not recorded_identity:
                    classification = "UNKNOWN"
                    detail = "pid exists but no identity was recorded at launch time"
                elif current.as_string() == recorded_identity:
                    classification = "RUNNING"
                    detail = "pid exists and identity matches; orphaned from this supervisor instance"
                else:
                    classification = "INTERRUPTED"
                    detail = "pid exists but identity does not match recorded identity (pid reuse)"

            if classification == "RUNNING":
                db.append_run_event(
                    self.db_path,
                    run_id,
                    "lifecycle",
                    stream_parser.lifecycle_event("reconciliation_orphaned", pid=pid, detail=detail)["payload"],
                )
                outcomes.append({"run_id": run_id, "classification": classification, "detail": detail})
                continue

            db.update_run_state(
                self.db_path,
                run_id,
                expected_version=run["version"],
                new_state=classification,
                fields={"completed_at": iso_now()},
            )
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event(
                    "reconciliation_classified", pid=pid, classification=classification, detail=detail
                )["payload"],
            )
            outcomes.append({"run_id": run_id, "classification": classification, "detail": detail})
        return outcomes

    # ------------------------------------------------------------------
    # Convenience read/test helpers
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict | None:
        return db.get_run(self.db_path, run_id)

    def active_run_ids(self) -> list[str]:
        with self._active_lock:
            return list(self._active.keys())

    def wait_for_run(self, run_id: str, timeout: float | None = None) -> dict:
        """Block until `run_id` leaves this instance's active-run registry
        (i.e. reaches a terminal state) or `timeout` elapses."""
        with self._active_lock:
            active = self._active.get(run_id)
        if active is not None:
            active.done_event.wait(timeout=timeout)
        return db.get_run(self.db_path, run_id)
