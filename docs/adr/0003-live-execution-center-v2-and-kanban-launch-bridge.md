# ADR 0003 — Live Execution Center v2: runtime.db as Execution Source of Truth, Kanban Launch Bridge

Status: **Accepted, implemented.**

## Context

Engineering Control Center v2 Increment 1 (ADR 0001/0002) made Launch resolve
the right workspace and branch for a task. It did not make that launch
*observable*. The real, production Launch button (`app.py`'s
`render_agent_launcher`, used from the Kanban Task Card, AI-агенты, Project
Chat, and the generated-task preview) called
`launch_service.execute_agent_launch`, which blocks the whole Streamlit
script inside `st.spinner` until `agent_runner.run_claude_code`'s synchronous
`subprocess.run` returns. There is never a moment, from that button, where a
background process exists for any UI to observe — so no amount of dashboard
redesign could have closed the gap without also changing what Launch itself
does.

Separately, a v2 Session Supervisor (`command_center/runtime/*`, Sprint 1)
already existed with real, non-blocking `Popen`-based launch, PID/process-
identity tracking, startup reconciliation, and process-group cancellation —
but it was only reachable through a disconnected "Live Execution Center" page
that launched its own ad-hoc runs, never bound to a real Kanban task.

## Decision

### 1. `runtime.db`'s `run`/`session`/`task` tables are the sole execution-state source of truth

Every "is this agent actually running" question is answered by reading a
`run` row (via `ExecutionCenterAPI`/`command_center.runtime.db`), reconciled
against real OS process state (`Supervisor.reconcile()`) — never by trusting
a task JSON field. This was already true for the v2 Supervisor; this
increment does not introduce a second store or a second execution engine —
it makes this the *only* store real production launches feed.

### 2. The real Kanban Launch button is bridged onto the v2 Supervisor

`command_center/launch_service.py` gains `execute_agent_launch_v2`, an async
counterpart to the untouched `execute_agent_launch`: it performs the same
pre-launch bookkeeping (`push_prompt_history`, `launch.begin_launch`), then
calls `ExecutionCenterAPI.start_run(task_id=<real kanban task id>, ...)`
instead of blocking on `executor.launch(...)`. `render_agent_launcher`'s
validation/workspace-resolution UI is unchanged across all 5 call sites;
only what happens after the button click changes — a real, PID-tracked,
cancellable run starts, and the user is redirected to Live Execution Center
to watch it, instead of blocking behind a spinner.

`execute_agent_launch` (old, synchronous) is left completely untouched and
still present, but as of this increment **has no remaining call site in
`app.py`** — it is kept only for interface stability and its own regression
tests (`tests/test_launch_service.py`), not as a live fallback path that can
run in parallel with the async one. The two cannot race each other because
nothing currently invokes the old path at all.

### 3. `Supervisor.start_raw` gains an explicit, narrow validation bypass — never a blanket one

The v2 Supervisor's existing `agent_runner.validate_repository` requires
`repository_path` to equal the project's *configured* `repository_path` —
a check the v1.2 synchronous flow never enforced, and one that would reject
a task's own worktree on a different path (the exact scenario ADR 0002's
workspace resolution enables). `start_raw`/`ExecutionCenterAPI.start_run`
gained `repository_already_validated: bool = False` (default preserves the
original strict check for every existing caller, including the ad-hoc v2
form). It is set to `True` only by `execute_agent_launch_v2`, and only when
`launch.validate_launch` actually ran and reported `can_launch=True` — gated
on `validation.can_launch`, not merely `validation is not None`, so a caller
that forwards a *failed* validation object can never bypass the stricter
check by accident.

### 4. Duplicate-active-run prevention

Neither the old nor the original v2 code prevented two concurrently-active
runs against the same task or the same resolved workspace — a real hazard
(two agents mutating one working tree at once) that async launching makes
easier to trigger than the old blocking flow's incidental serialization.
`launch_service.find_active_run_conflict` scans every currently-active run
(`runtime_db.EXECUTION_CENTER_ACTIVE_STATES`) for a match on `task_id` *or*
resolved `repository_path`, and both launch entry points —
`execute_agent_launch_v2` and the ad-hoc `render_execution_center_launch_form`
— refuse to launch (raising `DuplicateActiveLaunchError` / showing an error)
before any task mutation or subprocess is spawned if one is found.

### 5. Reconciliation runs on every dashboard refresh, not just at startup

`Supervisor.reconcile()` was written for one-time startup reconciliation
(classifying whatever a *previous*, crashed Supervisor instance left
`RUNNING`). The mission requires reconciling on every refresh tick, which
this increment does via `get_execution_center_api()` (calls `.reconcile()`
once at construction — functionally "on app restart" for a
`st.cache_resource`-cached singleton) and `task_sync.reconcile_and_sync`
(calls it again every refresh). Calling it repeatedly during *normal*
operation — while the *same* instance is still actively supervising a run —
uncovered a real race: a fast-exiting process could finish before its own
`_supervise` thread wrote the terminal state, and `reconcile()` would see
"pid gone" and misclassify a normally-completing run as `INTERRUPTED`.
Fixed in `Supervisor.reconcile()` itself: it now skips any run currently in
the instance's own `self._active` registry — such a run is definitionally
not orphaned, and its own background thread is the sole authority for its
terminal state.

### 6. Task JSON is a one-way, conservative projection — never re-derived as truth

`command_center/runtime/task_sync.py`'s `sync_task_from_run` reads a `run`
row fresh every time and writes only: `launch_status` (mapped from the
session's display status — never a new value added to
`models.LAUNCH_STATUSES`), `current_run_id`, and — once, at terminal state,
guarded so it never re-fires for the same run — `report_path`,
`repository_path`, `branch` (from a live git-status read of the run's own
workspace), `last_run_at`, `latest_verdict`, `pull_request_url` (both parsed
deterministically from the run's final result text via the existing
`report_parser`, the same parser v1.2 already used). It never touches
`progress`, `progress_mode`, `workflow_stage`, or `current_stage` — those
remain governed entirely by existing v1.2 rules and manual user action.
Tasks never launched through the v2 bridge have no `current_run_id` and are
left completely alone by `reconcile_and_sync`.

### 7. Derived heartbeat — a liveness probe, never an agent-emitted signal

The runtime has no heartbeat concept: no run/session row records "last
alive at," and the agent process itself emits none. Instead of fabricating
one, the dashboard performs a cheap, read-only liveness probe
(`identity.capture_identity(pid) is not None` — the same primitive
`Supervisor.reconcile()` uses, never a signal) once per refresh tick for
every `Running` session, and keeps the wall-clock time of the last
successful probe in `st.session_state` only — never written to `runtime.db`,
so it never grows the database on every 2–5s tick, and it resets to
"unknown" on an app restart until the next tick re-probes. Every rendering
of this value is labeled "проверка живости UI, не сигнал агента" (a UI
liveness check, not an agent signal), never "heartbeat" alone.

### 8. Cancellation is unchanged, and gated by an explicit server-side re-check

`Supervisor.cancel()` (SIGTERM to the run's process group, SIGKILL after a
grace period if needed) is not modified by this increment. The dashboard's
Cancel button requires an explicit confirmation checkbox, and — like every
other confirm-then-act control in this codebase — re-checks that
confirmation server-side inside the click handler before ever calling
`request_cancel`, not relying on the button's client-side `disabled=`
attribute alone (which a test harness, or in principle a malformed client
request, does not have to respect).

## Session model (additive migration 3, `run` table)

`expected_branch` (resolved once, at launch: `task.branch` → `project.
default_branch` → `NULL`), `launch_source` (`"kanban_task"` /
`"execution_center_adhoc"`), `prompt_version` — write-once at `create_run`,
never updated afterward. `commit_hash`, `pull_request_url` — set once, at
terminal-state task sync, via the new `db.set_run_result_fields`. No new
persisted status value is introduced anywhere: `session_view.derive_status`
is a display-only mapping of the existing `RUN_STATES` enum.

## Consequences

- **Positive**: the actual production Launch button is now real-process
  observable — Running/Waiting/Requires Attention/Completed/Failed reflect
  `runtime.db`, reconciled against real OS processes, not a task field.
- **Positive**: zero blast radius on the frozen Sprint 1 Supervisor's launch/
  cancel/reconcile mechanics beyond the two additive parameters and the one
  `reconcile()` race fix described above — every pre-existing Supervisor test
  passes unmodified.
- **Trade-off accepted**: workflow-stage/progress auto-advancement
  (verdict-driven stage jumps, "Create Next Task" suggestion) is not ported
  to the async path in this increment — only `launch_status`/report/verdict/
  PR fields sync automatically, per the mission's "never rewrite progress
  unless existing rules allow it."

## Known limitations (out of scope for this increment)

- **`Cancelled` maps to Kanban `launch_status="Failed"`** — `models.
  LAUNCH_STATUSES` has no dedicated Cancelled value; flagged rather than
  silently invented.
- **Heartbeat is session-state-only** — resets on app restart until the next
  refresh tick re-probes; disclosed, not a bug.
- **`expected_branch`/`launch_source`/`prompt_version` are write-once** —
  a run launched before this increment, or through the ad-hoc v2 form
  (which does not resolve a task-aware expected branch), shows `NULL`/"—"
  for these fields; never backfilled.
- **The live `actual_branch`/git-status probe runs one real `git` subprocess
  per displayed session per refresh tick** — acceptable at today's expected
  scale (a handful of concurrent sessions), not load-tested beyond that.
- **A pre-existing (Sprint 1, out of scope) fragility in `identity.py`**:
  on macOS, `ps`'s reported command path for a *venv-symlinked* Python
  interpreter can differ between two captures of the same live process,
  which would read as "pid reuse" to `reconcile()`. Confirmed during manual
  smoke testing with a stand-in `claude` binary; the real `claude` CLI is an
  installed binary at a stable path, not a symlinked venv interpreter, and
  is not expected to exhibit this. The existing test suite already avoids it
  (`tests/test_runtime_reconciliation.py` uses `sleep`, not a Python
  interpreter, for its "still alive" cases) — not something this increment
  changes or is in scope to fix.
- **Manual progress override protection is structural, not tested against
  malicious task JSON** — `task_sync` simply never writes those fields; a
  task record's `progress`/`progress_mode`/`workflow_stage` cannot be
  touched by anything in this module, by construction.

## Verification

`ruff check .`, `python -m compileall -q .`, `pytest -q`, `git diff --check`
— all clean. See the founder-review report for the exact pass count and the
Streamlit `AppTest` suites exercised.
