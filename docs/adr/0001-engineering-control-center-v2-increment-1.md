# ADR 0001 — Engineering Control Center v2, Increment 1

Status: **Accepted, implemented.**

## Context

AI Command Center's mission was extended: become the primary Engineering
Control Center for the full software development lifecycle across
repositories — task creation, launch, live execution, review, and
completion, in one place. The full mission spans roughly eighteen feature
areas (task model, launch system, project dashboard, kanban, live execution
center, engineering timeline, dependency graph, next-task recommender,
engineering supervisor, engineering health, prompt library, workspace
manager, reporting, global search, multi-executor support, automation
safety).

Two existing facts shaped how this had to be built, discovered before
writing any code:

1. **Two parallel, unsynced task/run stores already exist**: `data/
   tasks.json` (v1.1 Kanban — priority/status/dependencies) and `data/
   runtime.db` (v2 SQLite Session Supervisor — async run state machine,
   process reconciliation). The mission's task model (title, project, PR,
   branch, progress...) is v1.1-shaped, so it extends `tasks.json` rather
   than migrating to SQLite.
2. **`workspace_home.py`/`executive` already implement large fractions of
   "Project Dashboard"/"Engineering Health."** Rebuilding those would
   duplicate real, working code.
3. A separate, already-approved architecture track exists at `docs/
   desktop/` (status D0, branch `feature/desktop-architecture-d0`) that
   commits this codebase to an eventual native PySide6/Qt desktop
   application reusing `command_center/*` **verbatim** — see §5 below. That
   track had not started implementation (D1 has explicit "no changes to
   `app.py` or any existing `command_center/*` module" in its scope), but
   its binding decisions constrain how new `command_center/*` code should
   be shaped from this point on.

Given the size of the full mission, work was split into **Tier A**
(implement now, to full quality) and **Tier B** (explicit roadmap, not
built). This ADR covers Tier A only, delivered as **Increment 1** of this
initiative — not to be confused with "Desktop Increment 1 (D1–D4)" in
`docs/desktop/`, which is a distinct, separately-scoped, not-yet-started
migration track. The two are complementary: this increment's `command_center/*`
additions are exactly the kind of "existing core" the future
`command_center.application` adapter layer (`docs/desktop/ARCHITECTURE.md`
§1) will wrap, per binding decision 9 ("existing `command_center/*` runtime
and read-model modules are reused, not rewritten").

## Decision

### 1. Task model: extend `tasks.json`, not a new store

`command_center/models.py` gained the v2 task fields (`goal`, `prompt` +
history/version, `notes`, `executor`, `workspace_path`,
`pull_request_url`/`status`, `progress`/`progress_mode`/`current_stage`,
`started_at`/`finished_at`, `timeline`, `launch_status`/`launch_history`)
plus `EXECUTION_STAGES`/`STAGE_PROGRESS` (the mission's Created→Merged
0–100% ladder). `normalize_task_execution` backfills every field on load —
no schema migration, consistent with the existing JSON-store convention.

The one semantic shift: `title` used to hold the objective text
(`new_task_record`'s `title` param was literally fed the goal). It now
means a short, independent heading; old records get their existing text
copied into the new `goal` field and a derived short `title` — covered by
a dedicated backward-compatibility test.

### 2. `app.py` stays an orchestration layer; all new business logic moved into `command_center/*`

Every module below has **zero Streamlit imports** and is independently
unit-testable with plain `pytest` — no `st.*`, no `session_state`, no
`AppTest`. `app.py`'s job is: collect widget input → call one of these →
render the plain-data result.

| Module | Owns | Not responsible for |
|---|---|---|
| `command_center.tasks_repository` | `tasks.json` read/write, `normalize_task`, `new_task_record`, `update_task_status`, `delete_task` | Rendering |
| `command_center.launch` | Pre-launch validation (workspace/git/branch/detached-HEAD/dirty-tree checks via `git_info`), launch status state machine, `launch_history` bookkeeping | OS calls (delegates to `os_actions`), running the executor |
| `command_center.launch_service` | End-to-end launch orchestration: run-record lifecycle, calling the executor, parsing the report, applying every derived task field (progress/stage/timeline/PR/verdict) | Persistence (returns data; caller decides when/whether to save) |
| `command_center.executors` | The `Executor` interface + registry (`claude_code` working; `chatgpt`/`codex`/`gemini`/`human`/`remote_agent` declared, `NotImplementedError` stubs) | Anything executor-specific beyond `launch(...)` |
| `command_center.recommend` | `recommend_next_task` — pure scoring with an explanation | Nothing else; a plain read model |
| `command_center.task_view` | Task Card read-model helpers: memoized git-status lookups, dependency-graph DOT generation, timeline sorting, manual launch-status bookkeeping | Rendering (`app.py` turns its return values into widgets) |
| `command_center.os_actions` | The **only** `sys.platform` branching in the app: reveal-in-Finder, open-Terminal, copy-to-clipboard | Everything else — no other module branches on OS |

`app.py`'s `render_agent_launcher` is now a widget-collection wrapper: it
gathers the task type/prompt/timeout, calls `launch.validate_launch`,
renders warnings/buttons, and on confirm calls
`launch_service.execute_agent_launch(...)`, which returns a `LaunchOutcome`
it renders. It no longer contains the run-record lifecycle, report parsing,
or task-field-derivation logic that used to be inlined in a ~250-line
Streamlit function — that logic is `launch_service.execute_agent_launch`
now, callable identically from a non-Streamlit context.

An `on_task_state_changed` callback parameter on `execute_agent_launch`
(rather than the service calling `save_tasks` itself, which would couple
it to `app.py`) preserves the pre-refactor crash-resilience property —
persisting "Launching"/"Running" state to disk *before* the blocking
executor call — without the service module knowing anything about how or
whether its caller persists.

### 3. OS-specific branching consolidated into one module

`docs/desktop/ARCHITECTURE.md` §6/§8.1 declares a future
`command_center.platform` package the *only* one permitted to branch on
`sys.platform`, with a documented `reveal_in_file_manager(path)` contract
function. `command_center/os_actions.py` is the interim, Streamlit-app-only
equivalent: every `sys.platform` check in this increment's new code lives
there, nowhere else, and `reveal_in_file_manager` is named to match that
future contract directly. `launch.py`'s `open_folder_at`/`open_terminal_at`/
`copy_to_clipboard` are now thin delegations to it, so their public API is
unchanged for existing callers.

`open_terminal_at`/`copy_to_clipboard` are **not** part of the documented
D3 platform contract (Desktop Increment 1 is read-only and explicitly
excludes an embedded terminal — `docs/desktop/DESKTOP_INCREMENT_1.md` §1);
they exist in `os_actions.py` because the Streamlit Launch System needs
them now. When D3 is actually implemented, `os_actions.py`'s functions are
one plausible seed for that contract, not a preemptive redefinition of it.

### 4. Executor abstraction, not a hardcoded `agent_runner` call

`launch_service.execute_agent_launch` calls
`executors.get_executor(executor_id).launch(...)` rather than calling
`agent_runner.run_claude_code` directly. `claude_code` is the only working
executor; `chatgpt`/`codex`/`gemini`/`human`/`remote_agent` are declared
with a shared interface and raise `NotImplementedError` — satisfying "must
support future executors" without pretending to integrate providers that
don't exist. Adding a real second executor later is a new entry in
`executors.EXECUTORS`, not a change to the launch pipeline.

### 5. Automation-safety boundary

The Launch System never merges, pushes, switches or deletes a branch,
rebases, commits, stashes, resets, cleans, or otherwise rewrites Git
history — the mission's automation-safety requirement, made structural
rather than just documented: `launch.py`, `launch_service.py`,
`executors.py`, and `tasks_repository.py` are each covered by an AST-based
regression test (`tests/test_launch.py`, `tests/test_launch_service.py`,
`tests/test_executors.py`, `tests/test_tasks_repository.py`) asserting the
literal string `"git"` never appears as a string constant in that module's
source — so a future edit that tries to add a git-write call to any of
these modules fails CI, not just code review. The only git access anywhere
in the Launch System is read-only, through `git_info.py` (branch/dirty/
detached-HEAD checks) and `agent_runner.git_snapshot` (pre/post-run
snapshots) — both pre-existing, independently-documented read-only wrappers
this increment does not modify.

Blocking validation errors and dirty-tree/branch-mismatch warnings are
enforced in two layers, not one: the launch button's `disabled=` attribute
(the primary, user-visible gate) *and* an explicit server-side re-check in
`app.py` immediately after the click (`if not validation.can_launch: ...
return` / `if validation.warnings and not warnings_ack: ... return`) before
any call into `launch_service`. The second layer exists because a UI
`disabled` attribute alone is a widget-level convention, not a guarantee —
defense in depth for a launch action was judged worth the four extra lines.

### 6. Pause/Resume/Restart are advisory status labels, not process control

The v1.1 executor path (`agent_runner.run_claude_code`) is a synchronous
`subprocess.run` call — once started, nothing in this codebase can pause or
resume it mid-flight; there is no supervisor process for the v1 runner to
signal (that capability exists only for the separate v2 Session
Supervisor, untouched by this increment). The Task Card's Pause/Resume/
Restart buttons therefore only ever call
`task_view.set_manual_launch_status`/`tasks_repository.set_manual_launch_status`
— they set `launch_status` to a planning marker and append a timeline
event; they do not signal, suspend, or kill any process. This is stated
directly in the UI (a caption beneath the buttons) so it is never
mistaken for real process control.

## Consequences

- **Positive**: every module in the table above is a candidate
  `command_center.application` adapter target verbatim, per the desktop
  track's binding decision 9 — no forked copy will be needed when that work
  starts.
- **Positive**: `app.py` grew by relatively little net logic despite the
  feature scope; the Task Card, Launch System, and recommendation engine
  are all backed by modules with their own unit tests, independent of
  Streamlit's `AppTest` harness.
- **Trade-off accepted**: `os_actions.py` is macOS-only (this repository's
  only current target for the Streamlit app) and explicitly not a claim on
  the final D3 `command_center.platform` contract's shape — Windows support
  and the exact signature convention (tuple-return vs. exception-based) are
  left to that future, separately-scoped increment.
- **Trade-off accepted**: `launch_service.execute_agent_launch` is
  synchronous/blocking, matching the existing v1.1 runner
  (`agent_runner.run_claude_code`) it wraps — it does not introduce
  background execution. A future async executor (or the v2 Session
  Supervisor) would need its own service function; this one intentionally
  keeps parity with what `agent_runner` already guarantees today.

## Known gaps in this increment (as shipped, not as originally scoped)

Surfaced by founder review; documented rather than closed here, since
closing either would mean adding new UI — out of scope for a review pass:

- **Manual progress override has no UI affordance.** `models.set_current_stage(...,
  mode="manual")`/`reset_progress_to_auto` are implemented, tested, and
  correctly block subsequent automatic advancement (`STAGE_PROGRESS`
  automation is a no-op once `progress_mode == "manual"`) — but no widget
  anywhere lets a user actually invoke the manual path. The invariant holds
  for any caller that does set it; the gap is reachability, not
  correctness.
- **No executor-selection UI exists.** `render_agent_launcher` hardcodes
  `executor_id="claude_code"`; the Task Card's executor badge is the only
  place `executors.get_executor(...)` is called. The non-`claude_code`
  entries fail cleanly and are unit-tested (`NotImplementedError`), but
  they are not "visibly disabled" anywhere in the UI, because there is no
  UI surface that lists them at all yet.

## Scope explicitly not built (Tier B)

Project Dashboard rebuild with a rolled-up Engineering Health score; Live
Execution Center elapsed-timer/waiting-queue rebuild; a true *periodic*
Engineering Supervisor; standalone Engineering Health/Workspace
Manager/Reporting pages; global search; real ChatGPT/Codex/Gemini/Remote
executor integrations. Each consumes data this increment already produces
(progress, timeline, launch history, executor field) and is additive, not
blocked, follow-up work.

## Verification

`ruff check .`, `python -m compileall -q .`, `pytest -q` (441 passed),
`git diff --check`, and an `AppTest`-based smoke pass across
dashboard/kanban/executive/focus/create confirming no exceptions —
all green as of this increment. See commit history for exact numbers at
time of merge.
