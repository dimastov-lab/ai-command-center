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
import json
import signal
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from command_center import agent_runner, project_config
from command_center.models import iso_now
from command_center.runtime import context_service, db, identity, outcome, providers, reports, stream_parser

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
MAX_PROVIDER_DIAGNOSTIC_EVENTS = 64
MAX_PROVIDER_DIAGNOSTIC_BYTES = 32_768

# Defense in depth: `build_claude_command` never constructs these, but every
# command is checked against this set before it is ever handed to `Popen`.
_FORBIDDEN_FLAGS = frozenset({"--continue", "-c", "--background", "--bg"})


class SupervisorError(Exception):
    """Raised for a launch/cancel request that cannot be carried out."""


class WorkspaceLockedError(SupervisorError):
    """Raised by `start_raw` when another run is already active
    (`db.EXECUTION_CENTER_ACTIVE_STATES`) against the same resolved
    workspace — wraps `db.WorkspaceLockedError` (the atomic, race-free check
    performed inside `db.create_run`'s own transaction) so callers that
    already catch `SupervisorError` (e.g. `app.py`'s launch handlers) need no
    new except clause, while a caller that wants the conflicting run
    specifically can catch this subclass and read `.conflicting_run`."""

    def __init__(self, conflicting_run: dict) -> None:
        self.conflicting_run = conflicting_run
        super().__init__(
            f"Workspace {conflicting_run['repository_path']!r} already has an active run "
            f"({conflicting_run['id']!r}, state={conflicting_run['state']!r}). Wait for it to "
            "finish or cancel it before launching again."
        )


class ProviderUnavailableError(SupervisorError):
    """The selected provider cannot be used safely on this machine."""


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
    stream-json --include-partial-messages --verbose --setting-sources ""
    --permission-mode <profile mode>`. Resume: identical, except `--resume
    <uuid>` (the *exact* id — never a bare `--resume` picker, never
    `--continue`) in place of `--session-id`.

    `--permission-mode` (via `agent_runner.PERMISSION_MODE_BY_PROFILE`) was a
    genuine gap here until this fix: without it, the CLI's implicit default
    permission mode denies `Write`/`Edit` tool calls outright in headless
    `-p` mode — confirmed empirically against the real `claude` CLI — while
    the process itself still exits 0, so a `trusted_development` run could
    silently fail to make any of the changes it was asked for and still be
    recorded `COMPLETED` (see `agent_runner`'s profile docstring and
    `runtime.outcome` for the terminal-state classifier that also guards
    against exactly this). `agent_runner.build_command` (the v1 synchronous
    executor) already set this; this was the divergence between the two.
    """
    profile = agent_runner.profile_for_task_type(task_type)
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
        "--permission-mode",
        agent_runner.PERMISSION_MODE_BY_PROFILE[profile],
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
    def __init__(
        self,
        *,
        process: subprocess.Popen,
        run_id: str,
        provider: providers.ExecutionProvider,
        provider_runtime: providers.ProviderRuntime,
    ) -> None:
        self.process = process
        self.run_id = run_id
        self.provider = provider
        self.provider_runtime = provider_runtime
        self.done_event = threading.Event()
        # Set by `_timeout_watchdog` (never by `cancel()`) so `_supervise` can
        # tell a timeout-triggered termination apart from an explicit,
        # human-confirmed cancellation when it decides the final state.
        self.timeout_triggered = threading.Event()
        # Set the first time the provider runtime recognizes readiness
        # evidence — the in-memory guard that makes the `first_output_at` /
        # `handshake_received` write happen exactly once per run (see
        # `_record_handshake`), without re-reading the run row on every line.
        # `handshake_lock` makes the check-and-set atomic across the two
        # concurrent reader threads, so a run whose stdout and stderr both
        # produce their first line at the same instant still records the
        # milestone exactly once.
        self.handshake_recorded = threading.Event()
        self.handshake_lock = threading.Lock()
        self.valid_result_recorded = threading.Event()
        self._diagnostic_lines: list[str] = []
        self._diagnostic_bytes = 0
        self._diagnostic_lock = threading.Lock()

    def add_diagnostic(self, text: str) -> None:
        encoded = text.encode("utf-8", errors="replace")
        with self._diagnostic_lock:
            if len(self._diagnostic_lines) >= MAX_PROVIDER_DIAGNOSTIC_EVENTS:
                return
            remaining = MAX_PROVIDER_DIAGNOSTIC_BYTES - self._diagnostic_bytes
            if remaining <= 0:
                return
            bounded = encoded[:remaining].decode("utf-8", errors="ignore")
            self._diagnostic_lines.append(bounded)
            self._diagnostic_bytes += len(bounded.encode("utf-8"))

    def diagnostic_lines(self) -> list[str]:
        with self._diagnostic_lock:
            return list(self._diagnostic_lines)


def _capture_stable_process_identity(
    process: subprocess.Popen, *, attempts: int = 20, interval_seconds: float = 0.01
) -> identity.ProcessIdentity | None:
    """Wait briefly for shebang/interpreter exec transitions to settle.

    Persisting an identity from the transient `/usr/bin/env` phase makes the
    same live child look like PID reuse milliseconds later. Two consecutive
    identical captures retain full start-time+command protection while
    avoiding that false mismatch.
    """
    previous = None
    consecutive = 0
    for _ in range(attempts):
        if process.poll() is not None:
            return None
        current = identity.capture_identity(process.pid)
        if current is not None and previous is not None and current.as_string() == previous.as_string():
            consecutive += 1
            if consecutive >= 1:
                return current
        else:
            consecutive = 0
        previous = current
        time.sleep(interval_seconds)
    return None


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
        # Run ids this instance has committed to launching (persisted as
        # QUEUED) but has not yet `Popen`'d — the gap `self._active` alone
        # cannot cover, because `_active` is only populated once a process
        # actually exists (see `_launch_process`). Guarded by the same
        # `_active_lock`. See `reconcile()` for why this must be included in
        # its "don't touch this, it's mine" guard, not just `self._active`.
        self._launching: set[str] = set()
        self._active_lock = threading.Lock()
        self._autopilot_thread: threading.Thread | None = None
        self._autopilot_stop: threading.Event | None = None
        # Opt-in: an env flag auto-starts the completion autopilot for a
        # backend process (e.g. a headless/desktop host). Off by default, so
        # tests and the plain Streamlit app never spawn it implicitly.
        if os.environ.get("AICC_COMPLETION_AUTOPILOT"):
            self.start_completion_autopilot()

    # ------------------------------------------------------------------
    # Completion pipeline (AICC-AUTONOMY-001) — advances the post-execution
    # lifecycle *independently* of the original Claude process. Bounded (only
    # due, non-terminal rows) and never blocks the UI: it is invoked either
    # from the opt-in background autopilot thread below or on demand.
    # ------------------------------------------------------------------

    def advance_completions(self, *, now=None, limit: int = 50, github=None) -> list:
        from command_center.runtime.completion_service import CompletionOrchestrator

        orchestrator = CompletionOrchestrator(self.db_path, github=github)
        return orchestrator.advance_pending(now=now, limit=limit)

    def start_completion_autopilot(self, *, interval_seconds: float = 30.0) -> None:
        """Start a bounded, daemon background poller that advances due
        completion rows. Idempotent — a second call while one is running is a
        no-op. This is the "Supervisor advances completion workflows
        independently of the Claude process" integration, kept off the UI
        thread so completion validation/GitHub calls never block rendering."""
        if self._autopilot_thread is not None and self._autopilot_thread.is_alive():
            return
        stop = threading.Event()
        self._autopilot_stop = stop

        def _loop() -> None:
            while not stop.wait(interval_seconds):
                try:
                    self.advance_completions()
                except Exception:  # noqa: BLE001 - one bad tick must never kill the poller
                    continue

        thread = threading.Thread(target=_loop, name="completion-autopilot", daemon=True)
        self._autopilot_thread = thread
        thread.start()

    def stop_completion_autopilot(self) -> None:
        if self._autopilot_stop is not None:
            self._autopilot_stop.set()

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
        expected_branch: str | None = None,
        launch_source: str | None = None,
        prompt_version: int | None = None,
        repository_already_validated: bool = False,
        executor_id: str = providers.CLAUDE_ID,
        canonical_repository_path: str | None = None,
    ) -> dict:
        """Prepare and launch a run from an already-final `prompt` string.

        `expected_branch`/`launch_source`/`prompt_version` are opaque,
        write-once Live Execution Center v2 metadata (see
        `command_center.runtime.session_view`/`task_sync`) — this method
        never inspects or validates them, just forwards them to
        `db.create_run` for later display/sync.

        `repository_already_validated`, default `False`, preserves the
        original v2 behavior for every existing caller: `repository_path`
        must equal `project`'s *configured* `repository_path`
        (`agent_runner.validate_repository`'s security boundary against an
        arbitrary/untrusted path). Set it only when the caller has already
        independently validated `repository_path` through an equivalent or
        stronger check — today, only `launch_service.execute_agent_launch_v2`
        does, via `launch.validate_launch` (existence, is-a-directory,
        is-a-git-repo) on the exact path `launch.resolve_workspace_path`
        already resolved (task workspace, else project default workspace,
        else project repository — see `docs/adr`). This is what makes a
        task's own worktree on its own feature branch launchable at all: the
        v1.2 synchronous flow (`agent_runner.run_claude_code`) never enforced
        project-repository equality either, only `launch.validate_launch`'s
        checks, so this keeps the v2 bridge exactly as permissive as the
        flow it replaces — never more.

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
        provider = providers.get_provider(executor_id)
        project_config.require_execution_provider_allowed(project, executor_id)
        context_service.require_launch_confirmation(confirmed, what=f"Launching a {provider.label} run")

        availability = provider.availability()
        if executor_id != providers.CLAUDE_ID and not availability.available:
            raise ProviderUnavailableError(f"{provider.label} unavailable ({availability.code}): {availability.message}")
        try:
            provider.validate_prompt(prompt)
        except ValueError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        supplied_repo_path = Path(repository_path).expanduser()
        if provider.requires_dedicated_worktree:
            supplied_absolute = supplied_repo_path.absolute()
            if supplied_absolute != supplied_absolute.resolve() or supplied_repo_path.is_symlink():
                raise SupervisorError("Unsafe Codex target: symlinked worktree paths are not permitted.")

        if repository_already_validated:
            repo_path = supplied_repo_path.resolve()
            if not repo_path.is_dir():
                raise SupervisorError(f"Workspace not found: {repo_path}")
        else:
            repo_path = agent_runner.validate_repository(project, repository_path)

        if provider.requires_dedicated_worktree:
            self._validate_dedicated_worktree(
                repo_path,
                canonical_repository_path=canonical_repository_path,
                expected_branch=expected_branch,
            )

        safe_default_title = "Codex CLI run" if executor_id == providers.CODEX_ID else prompt[:120]
        persisted_title = safe_default_title if executor_id == providers.CODEX_ID else (title or safe_default_title)
        if task_id is None:
            task = db.create_task(
                self.db_path, project=project, title=persisted_title, task_type=task_type
            )
            task_id = task["id"]
        elif db.get_task(self.db_path, task_id) is None:
            db.create_task(
                self.db_path,
                project=project,
                title=persisted_title,
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

        try:
            spec = provider.build_launch(
                repository_path=repo_path,
                session_id=session_id,
                prompt=prompt,
                task_type=task_type,
                is_resume=is_resume,
                model=model,
            )
        except (RuntimeError, ValueError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        command = list(spec.argv)
        if executor_id == providers.CLAUDE_ID:
            _assert_no_forbidden_flags(command)

        try:
            run = db.create_run(
                self.db_path,
                session_id=session_id,
                task_id=task_id,
                project=project,
                task_type=task_type,
                repository_path=str(repo_path),
                prompt=prompt if executor_id == providers.CLAUDE_ID else "[redacted: prompt transported via stdin]",
                is_resume=is_resume,
                timeout_seconds=timeout_seconds,
                command=command,
                expected_branch=expected_branch,
                launch_source=launch_source,
                prompt_version=prompt_version,
                provider_id=executor_id,
                provider_metadata_json=providers.audit_json(spec.audit_metadata),
                enforce_workspace_lock=True,
            )
        except db.WorkspaceLockedError as exc:
            raise WorkspaceLockedError(exc.conflicting_run) from exc

        # From here on this run is committed to being launched by *this*
        # instance — recorded the instant the row exists (still PREPARED),
        # not after the QUEUED transition below, so a concurrent
        # `reconcile()` call in this same process (e.g. another browser
        # tab's dashboard refresh) — which now scans PREPARED rows too,
        # since that's exactly what this hardening added — can never
        # observe this row before it's guarded here. Registering it any
        # later would reopen the same gap this set exists to close: a
        # pid-less PREPARED/QUEUED row with no entry in `self._launching`
        # is indistinguishable from one abandoned by a crashed predecessor.
        # See `reconcile()`'s skip-guard and `_launch_process`'s `finally`,
        # which removes this once the row has a real process (or has
        # failed) recorded instead.
        with self._active_lock:
            self._launching.add(run["id"])

        try:
            run = db.update_run_state(self.db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
            pre_run_status = agent_runner.git_snapshot(repo_path).get("status_summary")
            run = db.update_run_fields(
                self.db_path,
                run["id"],
                expected_version=run["version"],
                fields={"pre_run_git_status": pre_run_status},
            )
        except Exception:
            # `_launch_process` (which owns clearing `self._launching` on
            # every path it can reach — success or a failed `Popen`) was
            # never entered, so nothing else will clear this run out of
            # `_launching` if it's left here.
            with self._active_lock:
                self._launching.discard(run["id"])
            raise

        return self._launch_process(run, spec, repo_path, provider)

    @staticmethod
    def _validate_dedicated_worktree(
        repo_path: Path, *, canonical_repository_path: str | None, expected_branch: str | None
    ) -> None:
        """Fail closed unless Codex targets the intended registered feature worktree."""
        from command_center import worktree_launcher

        if not canonical_repository_path:
            raise SupervisorError("Codex launch requires the project's canonical repository path.")
        canonical = Path(canonical_repository_path).expanduser().resolve()
        if repo_path == canonical:
            raise SupervisorError("Unsafe Codex target: the canonical checkout cannot be used for execution.")
        if not expected_branch:
            raise SupervisorError("Codex launch requires the intended task branch.")
        validation = worktree_launcher.validate_worktree(
            repository_root=canonical,
            worktree_path=repo_path,
            expected_branch=expected_branch,
            require_clean=True,
        )
        if not validation.can_launch:
            detail = "; ".join(validation.errors) or "worktree validation failed"
            raise SupervisorError(f"Unsafe Codex target worktree: {detail}")

    def _launch_process(
        self,
        run: dict,
        spec: providers.LaunchSpec,
        repo_path: Path,
        provider: providers.ExecutionProvider,
    ) -> dict:
        run_id = run["id"]
        provider_runtime = provider.create_runtime(
            prompt=spec.stdin_text if spec.stdin_text is not None else "",
            environment=spec.environment,
        )
        try:
            return self._launch_process_unguarded(
                run, spec, repo_path, provider, provider_runtime
            )
        finally:
            # Whatever happened above — a successful launch (`self._active`
            # now has `run_id`) or a failed `Popen` (state already FAILED) —
            # this run is no longer "committed to being launched but not yet
            # observable" (see `start_raw`'s `self._launching.add`). Runs
            # before `self._active[run_id] = active` so there is never a gap
            # where `run_id` is in neither set for a concurrent `reconcile()`
            # to see through.
            with self._active_lock:
                self._launching.discard(run_id)

    def _launch_process_unguarded(
        self,
        run: dict,
        spec: providers.LaunchSpec,
        repo_path: Path,
        provider: providers.ExecutionProvider,
        provider_runtime: providers.ProviderRuntime,
    ) -> dict:
        run_id = run["id"]
        try:
            process = subprocess.Popen(
                list(spec.argv),
                cwd=repo_path,
                stdin=subprocess.PIPE if spec.stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=spec.environment,
                text=True,
                bufsize=1,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            failure_reason = "executable_missing" if isinstance(exc, FileNotFoundError) else "provider_launch_failed"
            run = db.update_run_state(
                self.db_path,
                run_id,
                expected_version=run["version"],
                new_state="FAILED",
                fields={"completed_at": iso_now(), "failure_reason": failure_reason},
            )
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event(
                    "launch_failed", error_type=type(exc).__name__, failure_reason=failure_reason
                )["payload"],
            )
            return run

        pid = process.pid
        proc_identity = _capture_stable_process_identity(process)
        if provider_runtime.requires_verified_identity and proc_identity is None:
            self._terminate_owned_spawn(process)
            run = db.update_run_state(
                self.db_path,
                run_id,
                expected_version=run["version"],
                new_state="FAILED",
                fields={
                    "completed_at": iso_now(),
                    "exit_code": process.returncode,
                    "failure_reason": "process_identity_unavailable",
                },
            )
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event(
                    "process_identity_unavailable",
                    failure_reason="process_identity_unavailable",
                )["payload"],
            )
            return run
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

        active = _ActiveRun(
            process=process,
            run_id=run_id,
            provider=provider,
            provider_runtime=provider_runtime,
        )
        with self._active_lock:
            self._active[run_id] = active

        waiter = threading.Thread(target=self._supervise, args=(run_id, active, repo_path), daemon=True)
        waiter.start()

        if spec.stdin_text is not None:
            stdin_writer = threading.Thread(
                target=self._write_stdin,
                args=(run_id, active, spec.stdin_text),
                daemon=True,
            )
            stdin_writer.start()

        timeout_seconds = run.get("timeout_seconds")
        if timeout_seconds is not None:
            watchdog = threading.Thread(
                target=self._timeout_watchdog, args=(run_id, active, timeout_seconds), daemon=True
            )
            watchdog.start()

        return db.get_run(self.db_path, run_id)

    @staticmethod
    def _terminate_owned_spawn(process: subprocess.Popen, *, grace_seconds: float = 0.5) -> None:
        """Boundedly clean up a just-spawned child before it becomes RUNNING.

        The unreaped Popen handle proves ownership here, so its PID cannot
        have been reused. This helper is used only during launch failure,
        before the process is exposed as an active run.
        """
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=grace_seconds)
            return
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass

    def _write_stdin(self, run_id: str, active: _ActiveRun, prompt: str) -> None:
        try:
            active.process.stdin.write(prompt)
            active.process.stdin.close()
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event("prompt_delivered", transport="stdin")["payload"],
            )
        except (BrokenPipeError, OSError, ValueError) as exc:
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event("prompt_delivery_failed", error=type(exc).__name__)["payload"],
            )

    # ------------------------------------------------------------------
    # Streaming consumption (runs in background reader threads)
    # ------------------------------------------------------------------

    def _record_handshake(self, run_id: str, active: _ActiveRun) -> None:
        """Record the provider startup/handshake milestone exactly once.

        This is deliberately separate from `process_started`: a valid PID
        proves the process was created; provider-approved readiness evidence
        proves it reached its protocol startup milestone. For Claude the
        historical any-output rule remains; for Codex only recognized
        lifecycle JSON qualifies. The gap
        between the two is exactly the window in which a run is "started but
        early output not yet received" — surfaced to the UI as
        `session_view.STATUS_STARTING`, never as a failure.

        Best-effort and non-fatal by construction:

        - Guarded by an in-memory `threading.Event` so it runs once even
          though both reader threads (stdout and stderr) call it, and without
          re-reading the run row for every subsequent line.
        - Any database error (a lost compare-and-set race against a
          concurrent `cancel()`/watchdog write, the run already gone, ...) is
          swallowed. Handshake timing is observability, not correctness — it
          must never crash a reader thread or fail a run, and the run's
          terminal state is decided entirely from process-exit facts
          regardless of whether this ever succeeded.

        The append-only `handshake_received` lifecycle event is written first
        (it touches only `run_event`, never `run.version`, so it never races
        anything), then the `first_output_at` column is set best-effort so the
        live projection layer (`session_view.derive_status`), which reads only
        the run row, can tell STARTING from RUNNING.
        """
        with active.handshake_lock:
            if active.handshake_recorded.is_set():
                return
            # Claim the milestone first, atomically: even if the DB writes
            # below fail or race, we must never spin re-attempting on every
            # line, nor let the other reader thread also claim it.
            active.handshake_recorded.set()
        now = iso_now()
        try:
            db.append_run_event(
                self.db_path, run_id, "lifecycle", stream_parser.lifecycle_event("handshake_received", at=now)["payload"]
            )
        except Exception:
            pass
        try:
            run = db.get_run(self.db_path, run_id)
            if run is None or run.get("first_output_at"):
                return
            db.update_run_fields(
                self.db_path, run_id, expected_version=run["version"], fields={"first_output_at": now}
            )
        except Exception:
            # LostUpdateError (a concurrent cancel/terminal write landed
            # first), KeyError (run gone), or any other db hiccup — the
            # milestone is best-effort; the append-only event above already
            # captured the timing for the audit log.
            pass

    def _drain_stdout(self, run_id: str, active: _ActiveRun) -> None:
        process = active.process
        try:
            for chunk in process.stdout:
                for line in active.provider_runtime.feed_stdout(chunk):
                    self._persist_stdout_event(run_id, active, line)
        finally:
            for line in active.provider_runtime.flush_stdout():
                self._persist_stdout_event(run_id, active, line)
            try:
                process.stdout.close()
            except Exception:
                pass

    def _persist_stdout_event(self, run_id: str, active: _ActiveRun, line: str) -> None:
        event = active.provider_runtime.parse_stdout_line(line)
        if active.provider_runtime.stdout_event_is_readiness(line, event):
            self._record_handshake(run_id, active)
        if event is None:
            return
        if active.provider_runtime.event_is_valid_result(event):
            active.valid_result_recorded.set()
        if active.provider_runtime.event_is_provider_error(event):
            active.add_diagnostic(json.dumps(event["payload"], ensure_ascii=False, sort_keys=True))
        self._append_stream_event(run_id, event["event_type"], event["payload"])

    def _drain_stderr(self, run_id: str, active: _ActiveRun) -> None:
        process = active.process
        try:
            for chunk in process.stderr:
                for line in active.provider_runtime.feed_stderr(chunk):
                    self._persist_stderr_event(run_id, active, line)
        finally:
            for line in active.provider_runtime.flush_stderr():
                self._persist_stderr_event(run_id, active, line)
            try:
                process.stderr.close()
            except Exception:
                pass

    def _persist_stderr_event(self, run_id: str, active: _ActiveRun, line: str) -> None:
        if active.provider_runtime.stderr_line_is_readiness(line):
            self._record_handshake(run_id, active)
        event = stream_parser.stderr_event(line[:providers.MAX_PERSISTED_EVENT_CHARS])
        active.add_diagnostic(event["payload"]["line"])
        self._append_stream_event(run_id, event["event_type"], event["payload"])

    def _append_stream_event(self, run_id: str, event_type: str, payload: dict) -> None:
        """Persist one stream event, tolerating a concurrently deleted run.

        Stdout/stderr reader threads outlive the foreground launcher. If the
        run (or its task/session parent) is deleted while output is still
        draining, the append hits a FOREIGN KEY violation. That is a benign
        race — there is nothing left to persist for a run that no longer
        exists — so the reader thread stops recording quietly instead of
        dying with an unhandled exception. This mirrors how the codebase
        already treats a vanished run elsewhere (see `_record_handshake`).
        """
        try:
            db.append_run_event(self.db_path, run_id, event_type, payload)
        except sqlite3.IntegrityError:
            pass

    def _final_result_payload(self, run_id: str) -> dict | None:
        """The payload of the run's own `result`-type event (the last line of
        `claude -p --output-format stream-json`'s output — carries `result`
        text and, when applicable, a `permission_denials` array), or `None`
        if no such event was persisted. Called only after both stdout/stderr
        reader threads have joined (see `_supervise`), so every event this
        run will ever produce is already committed to `run_event`."""
        result_events = db.list_run_events(self.db_path, run_id, after_seq=0, limit=1_000_000, event_type="result")
        if not result_events:
            return None
        return result_events[-1]["payload"]

    def _supervise(self, run_id: str, active: _ActiveRun, repo_path: Path) -> None:
        process = active.process
        stdout_thread = threading.Thread(target=self._drain_stdout, args=(run_id, active), daemon=True)
        stderr_thread = threading.Thread(target=self._drain_stderr, args=(run_id, active), daemon=True)
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
        elif exit_code != 0:
            new_state = "FAILED"
            failure_reason = active.provider.classify_failure(
                exit_code=exit_code, diagnostic_lines=active.diagnostic_lines()
            )
        else:
            if active.provider_runtime.requires_valid_result and not active.handshake_recorded.is_set():
                new_state = "FAILED"
                failure_reason = "incomplete:provider_handshake_missing"
            elif active.provider_runtime.requires_valid_result and not active.valid_result_recorded.is_set():
                new_state = "FAILED"
                failure_reason = "incomplete:provider_result_missing"
            else:
                # exit_code == 0 only proves the provider process itself did
                # not crash. EvaluatingResult decides whether the requested
                # work genuinely completed.
                result_payload = self._final_result_payload(run_id)
                classification, reason = outcome.classify_process_result(
                    task_type=run["task_type"],
                    result_text=(result_payload or {}).get("result"),
                    permission_denials=(result_payload or {}).get("permission_denials"),
                    working_tree_changed=working_tree_changed,
                )
                if classification == outcome.OK:
                    new_state = "COMPLETED"
                else:
                    # Both remain FAILED in the authoritative state model;
                    # the prefix preserves the display distinction.
                    new_state = "FAILED"
                    failure_reason = f"{classification}:{reason}"

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
        if active.process.poll() is not None or (
            active.provider_runtime.requires_verified_identity
            and not identity.identity_matches(active.process.pid, run.get("process_start_identity"))
        ):
            raise SupervisorError(
                f"Run {run_id!r} process identity cannot be verified; "
                "refusing to signal a possibly reused PID."
            )

        for _ in range(5):
            try:
                run = db.update_run_fields(
                    self.db_path,
                    run_id,
                    expected_version=run["version"],
                    fields={"cancel_requested": 1, "cancel_requested_at": iso_now()},
                )
                break
            except db.LostUpdateError:
                run = db.get_run(self.db_path, run_id)
                if run is None or run["state"] != "RUNNING":
                    raise SupervisorError(
                        f"Run {run_id!r} changed state before cancellation could be recorded "
                        f"(now state={None if run is None else run['state']!r})."
                    ) from None
        else:
            raise SupervisorError(
                f"Run {run_id!r} was changing too quickly to record cancellation safely; retry."
            )

        db.append_run_event(
            self.db_path, run_id, "lifecycle", stream_parser.lifecycle_event("cancel_requested")["payload"]
        )

        pid = active.process.pid
        sigterm_sent = self._signal_verified_process_group(active, run, signal.SIGTERM)
        db.append_run_event(
            self.db_path,
            run_id,
            "lifecycle",
            stream_parser.lifecycle_event(
                "cancel_sigterm_sent" if sigterm_sent else "cancel_signal_refused_identity_mismatch", pid=pid
            )["payload"],
        )

        exited_in_time = active.done_event.wait(timeout=grace_seconds)
        if not exited_in_time:
            sigkill_sent = self._signal_verified_process_group(active, run, signal.SIGKILL)
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event(
                    "cancel_sigkill_sent" if sigkill_sent else "cancel_signal_refused_identity_mismatch", pid=pid
                )["payload"],
            )
            active.done_event.wait(timeout=grace_seconds)

        return db.get_run(self.db_path, run_id)

    @staticmethod
    def _signal_verified_process_group(active: _ActiveRun, run: dict, sig: signal.Signals) -> bool:
        """Signal only while the child still matches its launch identity."""
        pid = active.process.pid
        if active.process.poll() is not None:
            return False
        if active.provider_runtime.requires_verified_identity and not identity.identity_matches(
            pid, run.get("process_start_identity")
        ):
            return False
        try:
            # start_new_session=True makes pgid equal the launch PID. Avoid
            # resolving a potentially reused PID to an unrelated new pgid.
            os.killpg(pid, sig)
            return True
        except ProcessLookupError:
            return False

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
        run = db.get_run(self.db_path, run_id)
        sigterm_sent = self._signal_verified_process_group(active, run, signal.SIGTERM)
        db.append_run_event(
            self.db_path,
            run_id,
            "lifecycle",
            stream_parser.lifecycle_event(
                "timeout_sigterm_sent" if sigterm_sent else "timeout_signal_refused_identity_mismatch", pid=pid
            )["payload"],
        )

        exited_in_time = active.done_event.wait(timeout=grace_seconds)
        if not exited_in_time:
            sigkill_sent = self._signal_verified_process_group(active, run, signal.SIGKILL)
            db.append_run_event(
                self.db_path,
                run_id,
                "lifecycle",
                stream_parser.lifecycle_event(
                    "timeout_sigkill_sent" if sigkill_sent else "timeout_signal_refused_identity_mismatch", pid=pid
                )["payload"],
            )

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    def reconcile(self) -> list[dict]:
        """Inspect every run currently recorded in an active state
        (`db.EXECUTION_CENTER_ACTIVE_STATES` — `PREPARED`, `QUEUED`, or
        `RUNNING`) and classify it conservatively. Never signals a process
        based only on a reused pid, and never guesses that a run silently
        completed. Does not consult `claude agents --json` — this SQLite
        `run` table is the Supervisor's own lifecycle registry, entirely
        independent of the `claude` CLI's background-agent registry (which
        p-mode runs never touch anyway, since `--background`/`--bg` is
        prohibited everywhere in this module).

        `PREPARED`/`QUEUED` rows are included, not just `RUNNING`, because a
        Supervisor process can crash between `start_raw` creating the row
        and `_launch_process` actually `Popen`-ing it — without this, such a
        row would sit "active" forever (never reachable again once its
        Supervisor is gone), permanently occupying its workspace's lock (see
        `db.create_run`'s `enforce_workspace_lock`). In practice a `PREPARED`
        row never has a `pid` (nothing has attempted `Popen` yet at that
        point), so it always resolves to `INTERRUPTED` below; a `QUEUED` row
        can rarely carry a `pid` (the narrow window between recording it and
        persisting the `RUNNING` transition), in which case it goes through
        the exact same pid/identity classification as a `RUNNING` row.

        Skips any run currently in `self._active` **or** `self._launching` —
        a run *this* instance is actively supervising, or has committed to
        launching but not yet `Popen`'d, is never a candidate for
        reconciliation:

        - `self._active`: the opposite of orphaned — its own `_supervise`
          background thread already holds the real waitable-child handle and
          will write the authoritative terminal state itself the moment the
          process exits. Without this guard, calling `reconcile()`
          repeatedly during normal operation (the Live Execution Center v2
          dashboard's refresh tick calls it on every tick, not just at
          startup — see `task_sync.reconcile_and_sync`) would race a
          fast-exiting process: if the OS process happens to exit before
          this instance's own `_supervise` thread gets to `process.wait()`
          and persist the result, `identity.capture_identity(pid)` here
          would see "pid gone" and misclassify a run that is completing
          completely normally as `INTERRUPTED`.
        - `self._launching`: now that `PREPARED`/`QUEUED` rows are in scope
          above, a run this same instance is mid-`start_raw` for (row
          persisted as `PREPARED` or `QUEUED`, no pid yet — `_launch_process`
          hasn't called `Popen`) would otherwise look identical to a
          genuinely abandoned `PREPARED`/`QUEUED` row from a crashed
          predecessor and get misclassified `INTERRUPTED` out from under its
          own in-flight launch. `start_raw` adds the run id here immediately
          once `db.create_run` returns (while the row is still `PREPARED`),
          not after the subsequent `QUEUED` transition — registering it any
          later would leave exactly that window unguarded.

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
        - pid exists and its identity matches exactly -> classified/left as
          `RUNNING` (transitioning a matched `QUEUED` row explicitly, since
          we now have positive proof it is actually running), but flagged
          with a `reconciliation_orphaned` event and *not* re-registered as
          an actively supervised run: this Supervisor instance has no
          stdout/stderr pipe or waitable-child handle for a process it did
          not itself `Popen`, so it cannot resume incremental persistence
          and must not attempt to signal/cancel it.
        """
        outcomes = []
        with self._active_lock:
            actively_supervised_ids = set(self._active.keys()) | set(self._launching)
        for run in db.list_runs(self.db_path, states=db.EXECUTION_CENTER_ACTIVE_STATES):
            run_id = run["id"]
            if run_id in actively_supervised_ids:
                continue
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
                if run["state"] != "RUNNING":
                    # Only reachable for a matched QUEUED row (see docstring)
                    # — QUEUED -> RUNNING is an already-allowed transition.
                    run = db.update_run_state(
                        self.db_path, run_id, expected_version=run["version"], new_state="RUNNING"
                    )
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
