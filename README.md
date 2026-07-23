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
| Implemented and enabled by default | Streamlit UI; planning and Kanban; project context, reports, and generated-task views; current JSON/JSONL project-chat and activity stores; asynchronous local Claude CLI execution; persisted run events; cancellation and timeouts; execution queue; Portfolio parsing, intelligence, and guarded worktree launch; completion-state seeding and read-only completion status |
| Implemented but opt-in | Completion autopilot through `AICC_COMPLETION_AUTOPILOT`; OpenAI project-chat provider when its package and environment variables are supplied; project-specific completion policies that permit automatic merge or recovery |
| Legacy but still present | Synchronous Claude execution; `runs.jsonl` run journal; generated-task shell workflow |
| Designed or planned, not implemented | Native PySide6 desktop client and packaging; distributed execution; durable remote workers; seamless attachment to a subprocess after the hosting Python process restarts |

Normal task launches always require an explicit user action and confirmation. There is no general
autonomous task scheduler on `main`. Periodic Streamlit fragments refresh views and recompute
readiness; they do not launch queued work.

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

`scripts/start-ui.sh` activates `.venv` when present and forwards additional Streamlit arguments.
It does not install dependencies. Streamlit is an HTTP/WebSocket server and this repository does
not configure `server.address`; explicitly bind it to localhost when the UI must not be reachable
from the local network:

```bash
scripts/start-ui.sh --server.address localhost
```

There is no application authentication layer.

## Application structure

- [`app.py`](app.py) is the direct Streamlit entry point. It configures the page, loads local
  state, owns navigation, renders top-level pages, caches the process-local execution API, and
  delegates domain work to `command_center/`.
- [`command_center/`](command_center/) contains storage, models, read models, launch services,
  Portfolio orchestration, runtime supervision, completion services, Git/GitHub adapters, and
  Streamlit UI components.
- [`command_center/runtime/`](command_center/runtime/) contains the SQLite runtime API,
  Supervisor, process reconciliation, task projection, and completion state machine.
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
| `data/runtime.db` | Authoritative SQLite state for runtime tasks, sessions, runs, run events, reports, and completion |
| `data/execution_queue.json` | Separate persisted planning/execution queue |
| `data/runs.jsonl` | Legacy append-only synchronous run journal |
| `data/chats.json`, `data/activity.jsonl` | Currently used project-chat and activity stores, separate from SQLite |
| `data/project_config.json` | Local project repository and completion-policy overrides |
| `data/portfolio_config.json` | Portfolio project-to-repository mapping |
| `data/portfolio_launches.json`, `data/portfolio_locks/` | Portfolio launch registry and coordination locks |
| `reports/<PROJECT>/` | Full Markdown execution and chat reports |
| `generated/<PROJECT>/` | Generated legacy task artifacts |

`data/tasks.json` remains the planning and Kanban task store. `data/runtime.db` is authoritative
for execution and completion state. The legacy `runs.jsonl` journal and the current JSON/JSONL
project-chat and activity stores coexist with SQLite, while `execution_queue.json` and Portfolio
registries, reports, and generated artifacts are additional persisted boundaries. Reconciliation
is therefore required: Execution Center refreshes project SQLite run/completion state back to
Kanban tasks, and queue readiness is recomputed from the planning store. These are projections, not
a single transactional database.

Most local artifacts are excluded by the checked-in [`.gitignore`](.gitignore). `runtime.db` is
local state and must remain untracked; the current checkout excludes it through repository-local
Git metadata rather than a checked-in ignore rule. A fresh clone should verify that exclusion
before running the application. Tests redirect data through `AICC_DATA_DIR`.

## Execution lifecycle

The primary launch path is asynchronous:

1. The user selects a task or ad-hoc instruction, repository, task type, and timeout.
2. Read-only preflight resolves the exact workspace and expected branch, validates repository
   state, and surfaces warnings.
3. The UI requires explicit confirmation before calling the runtime API.
4. The API persists task, session, and run records in `data/runtime.db`.
5. The Supervisor starts the Claude CLI with `Popen(shell=False, start_new_session=True)`,
   records PID identity, streams bounded stdout/stderr events, and returns control to Streamlit.
6. Reader and watchdog threads handle output, timeout, completion, and report persistence.
7. Cancellation signals the process group, escalating from termination to kill when needed.
8. Run-to-task reconciliation updates the Kanban projection and seeds or advances completion
   state.

Duplicate active runs for the same task or resolved workspace are rejected. The Supervisor owns
live `Popen` handles, pipes, and reader threads only inside the hosting Python process. SQLite
state survives restart, but stdout/stderr pipes and live process handles cannot be restored.
Startup reconciliation can inspect a persisted PID and its recorded identity to classify a run;
it cannot seamlessly resume supervision or reattach to the original child process.

The legacy synchronous `execute_agent_launch` and JSONL journal remain for compatibility and
tests. The Streamlit task-launch bridge uses the asynchronous runtime path.

## Execution queue

`data/execution_queue.json` is a separate whole-file persisted queue. Queue entries track planning
status, dependencies, readiness, and launch linkage. Readiness is recalculated when queue and UI
checkpoints run. The queue never launches a task by itself: the user must click the launch control
and pass the normal confirmation and preflight boundaries.

Queue loads and saves are atomic at the file-replacement level, but the queue does not currently
have the cross-process locked mutation primitive used by `data/tasks.json`. Concurrent
read-modify-write operations can therefore lose another writer's update.

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
6. persists the Portfolio launch registry and queue link;
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

- Portfolio orchestration may create a branch/worktree and may remove only resources it created
  while rolling back a failed pre-launch transaction.
- Completion Git operations may push or recreate a branch; force-push is rejected.
- The GitHub adapter may discover, create, and—only when completion policy permits—merge pull
  requests through fixed `gh` argument lists.
- Validation commands are parsed with `shell=False`, bounded by timeouts, and restricted to an
  executable allowlist.

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

The same mandatory gates run locally and in CI:

```bash
git diff --check
ruff check .
python -m compileall -q command_center scripts tests app.py
pytest -q
```

`.github/workflows/ci.yml` runs these gates for pull requests into `main`, pushes to `main`, and
manual dispatches on Python 3.14. The workflow uses a read-only token, pins actions to commit SHAs,
and cancels superseded runs for the same ref.

## Current limitations and risks

- Multiple persistence authorities require explicit reconciliation and cannot provide one atomic
  transaction across planning, runtime, queue, Portfolio, reports, and legacy journals.
- Legacy synchronous/JSONL and current asynchronous/SQLite execution paths coexist.
- Supervisor ownership is process-local; a server restart loses pipes and live `Popen` handles.
- `app.py` and several runtime/Portfolio service modules are large, concentrated change surfaces.
- There is no configured static type checker.
- Whole-file execution queue mutations are not protected by a cross-process lock.
- Streamlit may be reachable beyond localhost unless explicitly bound; the application has no
  authentication.
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
