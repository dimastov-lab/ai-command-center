# AI Command Center

AI Command Center is a local engineering control application for planning work, launching and
observing AI-assisted engineering runs, coordinating Portfolio tasks, and completing guarded
GitHub delivery workflows. The implemented user interface is a Streamlit application served by
`app.py`; the native PySide6 desktop client described under
[`docs/desktop/`](docs/desktop/README.md) is a planned architecture, not an implemented or
packaged product.

This repository is not a production, distributed, or remote-worker execution platform. It is a
single-host control plane whose durable state is local to the machine running Streamlit.

## Current capability status

| Classification | Capabilities |
|---|---|
| Implemented and enabled by default | Streamlit UI; planning and Kanban; project context, reports, and generated-task views; current JSON/JSONL project-chat and activity stores; asynchronous local Claude CLI execution; persisted run events; cancellation and timeouts; fail-closed task-workspace provisioning and verification; a persisted execution queue whose application-owned mutations are locked; Portfolio parsing, intelligence, and guarded worktree launch; completion-state seeding and read-only completion status |
| Implemented service/API foundations, not autonomously driven | Deterministic read-only scheduling decisions through `ExecutionCenterAPI.plan_schedule`; persisted, evidence-backed autonomy proposals through the runtime API/domain layer, surfaced in an operator approve/reject inbox (`ui/proposals_panel.py`). Neither capability has a background task driver, durable scheduling claim/lease, or automatic dispatcher |
| Implemented but opt-in | Completion autopilot through `AICC_COMPLETION_AUTOPILOT`; OpenAI project-chat provider when its package and environment variables are supplied; project-specific completion policies that permit automatic merge or recovery |
| Legacy but still present | Synchronous Claude execution; `runs.jsonl` run journal; generated-task shell workflow |
| Designed or planned, not implemented | Native PySide6 desktop client and packaging; distributed execution; durable remote workers; seamless attachment to a subprocess after the hosting Python process restarts |

Normal task launches require an explicit user action and confirmation. The scheduling API can
return advisory `ASSIGN`, `DEFER`, or `BLOCKED` decisions from a point-in-time snapshot, but it
does not persist a claim or launch anything. Periodic Streamlit fragments refresh views and
recompute readiness; no background task scheduler or automatic dispatcher is implemented.

## Getting started

Create an environment and install the declared runtime and test dependencies:

Supported Python: **3.14** (the version CI runs and the app is validated against).

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

The local Claude execution paths require an installed and authenticated `claude` CLI. Optional
environment variables are documented in [`.env.example`](.env.example). The application does not
load `.env` automatically.

Start the Streamlit server with either:

```bash
python -m streamlit run app.py
```

or:

```bash
scripts/start-ui.sh
```

`scripts/start-ui.sh` activates `.venv` when present, forwards additional Streamlit arguments,
and binds the server to localhost by default (the application has no authentication, so it must
not be reachable from the local network unless you explicitly opt in). It does not install
dependencies. To override the bind address, pass an explicit `--server.address`:

```bash
scripts/start-ui.sh --server.address 0.0.0.0   # explicitly expose (not recommended)
```

There is no application authentication layer.

## Application structure

- [`app.py`](app.py) is the direct Streamlit entry point. It configures the page, loads local
  state, owns navigation, renders top-level pages, caches the process-local execution API, and
  delegates domain work to `command_center/`.
- [`command_center/`](command_center/) contains storage, models, read models, launch services,
  fail-closed workspace provisioning, execution-queue and Portfolio orchestration, runtime
  supervision, completion services, Git/GitHub adapters, and Streamlit UI components.
- [`command_center/runtime/`](command_center/runtime/) contains the SQLite runtime API,
  Supervisor, process reconciliation, task projection, completion state machine, read-only
  scheduling planner, and autonomy proposal domain/service.
- [`command_center/ui/`](command_center/ui/) contains extracted Streamlit panels and renderers.
  Much of the application routing and presentation still remains concentrated in `app.py`.
- [`scripts/start-task.sh`](scripts/start-task.sh) is the legacy generated-task launcher. It
  currently recognizes only `AIOS`, `BANK`, and `LEGAL`.
- [`tests/`](tests/) contains unit, integration, concurrency, subprocess, Git/worktree, Portfolio,
  completion, and Streamlit `AppTest` coverage.

The current Streamlit navigation has 19 destinations: Dashboard, Workspace Home, Executive,
Create Task, Project Chat, Kanban, AI Agents, Live Execution Center, Run Journal, Timeline,
Projects, Generated Files, Reports, Context, Git Center, Workspace Launcher, Focus Mode,
Portfolio Execution, and Portfolio Overview.

## Persistence and sources of truth

AI Command Center deliberately has more than one persistence authority:

| Store | Role |
|---|---|
| `data/tasks.json` | Planning and Kanban task store |
| `data/runtime.db` | Authoritative SQLite schema 10 state for runtime tasks, sessions, runs, run events, reports, completion, and autonomy proposals/evidence/events |
| `data/execution_queue.json`, `data/execution_queue.lock` | Separate persisted planning/execution queue plus its same-host cooperative OS advisory lock |
| `data/runs.jsonl` | Legacy append-only synchronous run journal |
| `data/chats.json`, `data/activity.jsonl` | Currently used project-chat and activity stores, separate from SQLite |
| `data/project_config.json` | Local project repository and completion-policy overrides |
| `data/portfolio_config.json` | Portfolio project-to-repository mapping |
| `data/portfolio_launches.json`, `data/portfolio_locks/` | Portfolio launch registry and coordination locks |
| `reports/<PROJECT>/` | Full Markdown execution and chat reports |
| `generated/<PROJECT>/` | Generated legacy task artifacts |

`data/tasks.json` remains the planning and Kanban task store. `data/runtime.db` is authoritative
for execution, completion, and the autonomy-proposal lifecycle. Schema 10 contains the application
tables `task`, `session`, `run`, `run_event`, and `report`; the three completion tables; and
`proposal`, `proposal_evidence`, and `proposal_event`; `schema_version` tracks migrations. It does
not contain the execution queue or scheduler claims. The legacy `runs.jsonl` journal and the
current JSON/JSONL project-chat and activity stores coexist with SQLite, while
`execution_queue.json` and Portfolio registries, reports, and generated artifacts are additional
persisted boundaries. Reconciliation is therefore required: Execution Center refreshes project
SQLite run/completion state back to Kanban tasks, and queue readiness is recomputed from the
planning store. These are projections, not a single transactional database.

Most local artifacts are excluded by the checked-in [`.gitignore`](.gitignore), which
also covers `data/runtime.db` and its WAL/SHM sidecars. Tests redirect data through
`AICC_DATA_DIR`.

### Runtime history retention

`data/runtime.db` grows with every run event. Retention is **off by default** and
opt-in via environment variables, so existing installs and the test suite are
unaffected:

- `AICC_RUNTIME_RETENTION_DAYS=<N>` — on startup (after schema migration), delete
  `run_event` rows for runs that have been terminal for longer than `N` days. The
  terminal run row itself is kept (it remains visible in the Execution Center and
  to reconciliation); only the bulky per-output event history is pruned.
- `AICC_RUNTIME_VACUUM_ON_START=1` — run `VACUUM` after pruning to reclaim disk.
  VACUUM rewrites the database under an exclusive lock, so enable it only on a
  single-host install that can briefly pause other writers.

## Execution lifecycle

The primary launch path is asynchronous:

1. The user selects a task or ad-hoc instruction, repository, task type, and timeout.
2. For a normal task launch, `prepare_task_launch` resolves the selected workspace, source
   repository, expected branch, and base branch, then classifies the request as ready, requiring
   warning acknowledgement, provisionable, or blocked.
3. The UI requires explicit confirmation before any branch/worktree provisioning or runtime
   mutation.
4. The normal task-v2 path may provision a missing worktree offline with `git worktree add`, never
   falling back to the source repository, and must pass fail-closed `WorkspaceSpec` verification.
   Wrong repository ownership, detached or wrong branch, and a primary worktree used for feature
   work are hard failures; the selected status policy controls dirty/untracked checks.
5. A read-only preflight rejects an already-active task or workspace when observed. At run
   insertion, SQLite transactionally enforces exact-workspace exclusivity; task-id preflight is not
   a durable claim and can race with another launcher.
6. The API persists task, session, and run records in `data/runtime.db`.
7. On a POSIX host with `waitid(WNOWAIT)`, the Supervisor starts the Claude CLI with
   `Popen(shell=False, start_new_session=True)` and atomically records PID identity plus the
   `RUNNING` transition. Unsupported hosts fail closed before `Popen`.
8. Reader and watchdog threads handle output and timeout while process-group exit, durable terminal
   persistence, and report finalization remain separate milestones.
9. Cancellation and timeout serialize signal/exit/reap decisions against the captured launch-time
   PGID, drain descendants before reaping the leader, and escalate from termination to kill when
   needed. A post-exit cancellation is rejected rather than relabelling a completed run.
10. Run-to-task reconciliation updates the Kanban projection and seeds or advances completion
    state.

The Supervisor owns live `Popen` handles, pipes, and reader threads only inside the hosting Python
process. SQLite state survives restart, but stdout/stderr pipes and live process handles cannot be
restored. Startup reconciliation can inspect a persisted PID and its recorded identity to classify
a run; it cannot seamlessly resume supervision or reattach to the original child process.

The legacy synchronous `execute_agent_launch` and JSONL journal remain for compatibility and
tests. The Streamlit task-launch bridge uses the asynchronous runtime path. Low-level/ad-hoc
`start_run` calls may omit `WorkspaceSpec`; the fail-closed isolation guarantee above is scoped to
the normal task-v2 launch paths that supply it.

## Execution queue

`data/execution_queue.json` is a separate whole-file persisted queue. Queue entries track planning
status, dependencies, readiness, and launch linkage. Readiness is recalculated when queue and UI
checkpoints run. The queue never launches a task by itself: the user must click the launch control
and pass the normal confirmation and preflight boundaries.

Queue writes use atomic file replacement. Application-owned persisted read-modify-write cycles
(`enqueue_and_persist`, `dequeue_and_persist`, `reevaluate_and_persist`, and launch-result commit)
hold `data/execution_queue.lock` across the complete load-transform-save operation. This is a
same-host cooperative OS advisory lock, not a distributed lock. Raw `load_queue`/`save_queue`
remain available as uncoordinated primitives, and reads stay lock-free.

## Portfolio intelligence and execution

Portfolio integration reads task cards from a separate Portfolio checkout, including lane,
project, dependency, conflict, branch, worktree, launch-profile, and permission-profile metadata.
The Portfolio Overview computes read-only project health, dependency waves, cycles, critical
paths, readiness, capacity, and recommendations. These intelligence views are derived at read
time; they do not edit Portfolio cards.

Portfolio Execution:

1. parses and validates ready cards;
2. resolves the project to an explicitly configured local repository;
3. checks dependencies, conflicts, launch registry, branch, worktree, and collision constraints;
4. previews the launch plan;
5. after explicit user action, creates or attaches the task branch and Git worktree;
6. persists the Portfolio launch registry and uses the locked queue helpers for queue insertion or
   rollback;
7. launches through the same asynchronous Execution Center API.

Worktree creation and rollback are intentionally bounded. Existing worktrees are validated before
attachment, and rollback removes only resources created by the failed launch attempt. Portfolio
batch launch remains user-triggered and applies a concurrency cap; it is not autonomous scheduling.

## Completion pipeline

A terminal process result is not the same as a completed engineering task. The persisted
completion state machine can:

- resolve and run an allowlisted validation plan;
- verify branch and commit evidence;
- push a branch without force-push;
- discover or create a GitHub pull request through `gh`;
- wait for checks, reviews, or manual merge;
- optionally merge under project policy;
- recover a closed-unmerged pull request when explicitly enabled;
- verify that the result is reachable from the target branch before marking the task completed.

Completion state, attempts, validation results, and events are stored in `runtime.db`. Conservative
defaults require validation and a pull request, use manual merge, and disable recovery. The
completion autopilot is opt-in through `AICC_COMPLETION_AUTOPILOT` and is disabled by default.
When enabled, it advances due completion records in a process-local background thread; it is not a
general task scheduler. The Streamlit completion panel only displays persisted status; it does not
expose manual advancement controls. Programmatic on-demand advancement is available through the
runtime API.

## Git and GitHub safety boundaries

Read-only Git views and preflight checks do not mutate repositories. Other narrowly scoped
components do have write capabilities:

- Normal task-v2 workspace provisioning may run `git worktree add` only after explicit
  confirmation; the resulting path must pass source-repository, branch, isolation, and status
  verification before process launch.
- Portfolio orchestration may create a branch/worktree and may remove only resources it created
  while rolling back a failed pre-launch transaction.
- Completion Git operations may push or recreate a branch; force-push is rejected.
- The GitHub adapter may discover, create, and—only when completion policy permits—merge pull
  requests through fixed `gh` argument lists.
- Validation commands are parsed with `shell=False`, bounded by timeouts, and restricted to an
  executable allowlist. The allowlist scopes the *entry binary* (it blocks direct `sh`/`rm`/`curl`
  invocation); interpreter-class entries such as `python3`, `node`, `npx`, and `make` will run
  whatever code the operator-supplied arguments specify, so `validation_commands` is trusted
  operator configuration and must be reviewed like any other privileged setting.

Launches and warnings require explicit UI confirmation. Normal completion defaults preserve
manual merge. Commit, push, pull-request, and merge authority remains a privileged operational
boundary; enabling an opt-in policy or invoking a corresponding control must be treated as
explicit authorization. Launched implementation/remediation agents can edit files, but their
Claude tool configuration denies Git write commands. Review task types receive only read/search
tools.

## Validation

The test suite redirects local data through `AICC_DATA_DIR` and uses temporary report paths.
Tests mock scenarios that would otherwise invoke the real Claude CLI, so validation does not
launch real agent jobs or write to the normal runtime stores.

The same validation gates run locally and automatically in CI:

```bash
git diff --check
ruff check .
python -m compileall -q command_center scripts tests app.py
pytest -q
```

`.github/workflows/ci.yml` checks the committed diff for whitespace errors and runs Ruff, byte
compilation, and pytest for pull requests into `main`, pushes to `main`, and manual dispatches on
Python 3.14. The workflow uses a read-only token, pins actions to commit SHAs, and cancels
superseded runs for the same ref. The workflow does not itself configure branch protection;
repository settings must separately require the check if merges are to be blocked on it.

## Current limitations and risks

- Multiple persistence authorities require explicit reconciliation and cannot provide one atomic
  transaction across planning, runtime, queue, Portfolio, reports, and legacy journals.
- Legacy synchronous/JSONL and current asynchronous/SQLite execution paths coexist.
- Supervisor ownership is process-local; a server restart loses pipes and live `Popen` handles.
- `app.py` and several runtime/Portfolio service modules are large, concentrated change surfaces.
- A static type checker is now configured (permissive, non-strict) via `pyproject.toml` and
  surfaced as a non-blocking CI step; it is not yet a merge gate and the codebase is not fully typed.
- The checked-in CI workflow is automatic but does not itself enforce branch protection; the
  repository's current plan/settings must be checked before treating it as a required merge gate.
  Enable "Require status checks to pass before merging" on `main` with the `Quality gates` check
  to make the workflow a real gate.
- The execution-queue lock is same-host and cooperative; raw queue mutation primitives can bypass
  it, and there is no distributed coordination.
- Scheduler decisions are point-in-time advice, not persisted claims. Task-id, capacity, and
  within-plan workspace decisions can change before the separate explicit launch; only exact
  workspace exclusion is enforced transactionally by the runtime launch path.
- The autonomy proposal layer has an operator approve/reject inbox (`ui/proposals_panel.py`), but no
  automated evidence collectors, per-project
  policy resolver, background driver, or executor; dispatch records and returns a plan but does not
  perform it.
- Fail-closed workspace verification is scoped to normal task-v2 paths that supply a
  `WorkspaceSpec`; low-level/ad-hoc launches preserve their separate behavior.
- Streamlit is bound to localhost by default by `scripts/start-ui.sh`; passing an explicit
  `--server.address` overrides that. The application has no authentication, so do not bind it
  to a reachable interface.
- The system does not provide distributed execution, remote-worker durability, or seamless
  process resumption.
- Native desktop packaging is not implemented.

## Further documentation

- [Architecture](ARCHITECTURE.md)
- [Current operating state](CURRENT_STATE.md)
- [Completion pipeline operator guide](docs/completion-pipeline.md)
- [Accepted architecture decisions](docs/adr/)
- [Planned desktop documentation](docs/desktop/README.md)
- [Changelog](CHANGELOG.md)
