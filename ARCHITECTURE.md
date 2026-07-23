# AI Command Center — Architecture

This document describes the architecture implemented on `main`. Current code, tests, accepted
ADRs, and runtime operator documentation are authoritative if another document conflicts with this
description.

The current product is a Streamlit application. The native PySide6 design under
[`docs/desktop/`](docs/desktop/README.md) is a planned architecture only: no PySide6 client,
desktop package, or native installer is implemented.

## 1. System context

AI Command Center is a local engineering control plane. A browser connects to a Streamlit server
running `app.py`; that Python process reads local planning and Portfolio files, persists execution
state in SQLite, starts local Claude CLI subprocesses, inspects and mutates explicitly selected Git
worktrees through bounded services, and can use the local `gh` CLI for completion workflows.

It is not a distributed scheduler or a durable remote-worker platform.

```mermaid
flowchart LR
    Operator["Engineer in browser"] --> Streamlit["Streamlit server<br/>app.py"]
    Streamlit --> UI["command_center/ui"]
    Streamlit --> Services["command_center services"]

    Services --> Planning["data/tasks.json<br/>planning and Kanban"]
    Services --> Queue["data/execution_queue.json<br/>execution queue"]
    Services --> Runtime["data/runtime.db<br/>runtime and completion"]
    Services --> AppStores["chats.json and activity.jsonl<br/>active application stores"]
    Services --> Legacy["runs.jsonl<br/>legacy run journal"]
    Services --> Portfolio["Portfolio cards and<br/>Portfolio registries"]
    Services --> Artifacts["reports and generated files"]

    Services --> Supervisor["Runtime Supervisor"]
    Supervisor --> Claude["Local Claude CLI<br/>process group"]
    Services --> Git["Local Git repositories<br/>branches and worktrees"]
    Services --> GitHub["GitHub through gh CLI"]

    Streamlit -. "planned, not implemented" .-> Desktop["Native PySide6 client"]
```

Streamlit itself serves HTTP and WebSocket traffic. The repository does not configure
`server.address`, and `scripts/start-ui.sh` does not add a bind address. Operators must explicitly
bind Streamlit to localhost when network exposure is not intended. The application has no
authentication layer.

## 2. UI and service boundaries

### 2.1 Streamlit host

`app.py` is a direct Streamlit script and is re-executed top to bottom on interactions. It:

- configures the page and sidebar;
- applies staged cross-page navigation;
- loads planning tasks;
- caches one `ExecutionCenterAPI` and process-local Supervisor per Streamlit server process;
- performs startup runtime reconciliation once for that cached resource;
- renders top-level pages through a flat `if/elif` route;
- uses timed Streamlit fragments to poll execution and completion views.

The 19 current navigation destinations are Dashboard, Workspace Home, Executive, Create Task,
Project Chat, Kanban, AI Agents, Live Execution Center, Run Journal, Timeline, Projects,
Generated Files, Reports, Context, Git Center, Workspace Launcher, Focus Mode, Portfolio
Execution, and Portfolio Overview.

`app.py` remains a large presentation and orchestration surface. `command_center/ui/` contains
selected extracted panels and components, including the execution queue, recommendations, project
selection and intelligence, Portfolio execution and overview, and shared shell elements. The
primary Live Execution Center, completion-status, and Workspace Home renderers remain in `app.py`.
`command_center/ui/` may import Streamlit; domain, storage, and runtime services remain plain
Python.

### 2.2 Application and domain services

Important service groups are:

- `models.py`, `tasks_repository.py`, and `storage.py`: planning model normalization, locked task
  mutations, atomic JSON, and JSONL primitives;
- `project_config.py`: project registry, repository paths, sensitivity, and completion policies;
- `launch.py` and `launch_service.py`: workspace selection, read-only preflight, confirmation
  state, and legacy/current launch bridges;
- `execution_queue.py`: persisted queue entries and readiness;
- `portfolio_models.py`, `portfolio_config.py`, `portfolio_launch.py`, and
  `portfolio_intelligence.py`: Portfolio parsing, repository mapping, execution planning,
  worktree orchestration, registry coordination, and read-only intelligence;
- `worktree_launcher.py`: validated branch/worktree creation and attachment;
- `runtime/api.py`: UI-facing execution facade;
- `runtime/db.py`: SQLite schema, transactions, state guards, compare-and-swap updates, and
  workspace locks;
- `runtime/supervisor.py`: live subprocess ownership and lifecycle;
- `runtime/task_sync.py`: runtime/completion projection into planning tasks;
- `runtime/completion.py` and `runtime/completion_service.py`: completion state machine and
  side-effect orchestrator;
- `runtime/validation.py`, `runtime/git_ops.py`, `runtime/repo_state.py`, and
  `runtime/github.py`: bounded validation, Git writes, Git inspection, and GitHub operations.

### 2.3 External process adapters

Subprocess-facing code uses fixed argument arrays and `shell=False`. The main adapters are:

- Claude CLI execution;
- read-only Git inspection;
- worktree/branch orchestration;
- completion Git push and branch recovery;
- GitHub pull-request operations through `gh`;
- legacy `scripts/start-task.sh`;
- configured validation commands selected from an executable allowlist.

## 3. Persistence architecture

There is no single system-wide transactional store. Several authorities coexist:

| Source | Authoritative for | Mutation model |
|---|---|---|
| `data/tasks.json` | Planning and Kanban tasks | Whole-file JSON; atomic replacement; thread/cross-process lock through `data/tasks.lock` |
| `data/runtime.db` | Runtime tasks, sessions, runs, run events, reports, and completion state | SQLite schema version 5; WAL, busy timeout, foreign keys, guarded transitions, CAS versions, workspace locks |
| `data/execution_queue.json` | Separate execution planning queue | Whole-file JSON; atomic replacement; no cross-process locked mutation |
| `data/runs.jsonl` | Legacy synchronous run journal | Append-only JSONL; latest record per run is folded on read |
| `data/chats.json` | Active project chat conversations | Whole-file JSON |
| `data/activity.jsonl` | Active application activity journal | Append-only JSONL |
| `data/project_config.json` | Local repository and policy overrides | Whole-file JSON |
| `data/portfolio_config.json` | Portfolio project-to-repository mapping | Whole-file JSON |
| `data/portfolio_launches.json` | Portfolio launch-to-runtime registry | Whole-file JSON plus Portfolio lock files |
| Portfolio task cards | Portfolio lanes, task metadata, dependencies, conflicts, and launch hints | Parsed from the separate Portfolio checkout; intelligence views are read-only |
| `reports/<PROJECT>/` | Full Markdown reports and saved chat content | Per-file artifacts |
| `generated/<PROJECT>/` | Legacy generated task files | Per-file artifacts |

`data/tasks.json` remains the planning and Kanban task store. `data/runtime.db` is authoritative for
execution and completion state. SQLite does not replace the active chat and activity stores, the
legacy `runs.jsonl` journal, the execution queue, Portfolio registries, or artifact directories.

`data/runs.jsonl` and `data/activity.jsonl` use append-only JSON Lines rather than JSON arrays.
Each record is appended, flushed, and synced to disk without rewriting earlier records. This
supports incremental persistence, avoids whole-file rewrites for write-heavy journals, and
confines an interrupted or malformed write to one line. Readers skip malformed lines, and the
legacy run journal folds later snapshots by run ID to recover each run's current record.

Most runtime files are covered by the checked-in `.gitignore`. `runtime.db` must remain local and
untracked, but the current checkout's exclusion is repository-local Git metadata rather than a
portable checked-in ignore rule. `AICC_DATA_DIR` redirects data storage for tests.

### 3.1 Reconciliation boundaries

Because planning, execution, queue, and completion do not share one transaction:

- the runtime service projects terminal run state into the linked `tasks.json` task;
- terminal successful runs seed completion records;
- completion state projects launch status, workflow stage, PR state, and final progress into the
  planning task;
- queue readiness is recomputed against current planning dependencies;
- Portfolio registry entries link Portfolio task IDs to runtime run IDs;
- legacy run data remains independently readable and is not automatically converted into the
  SQLite source of truth during every startup.

Projection is idempotent and task mutations are locked, but reconciliation is still an explicit
application responsibility. A crash between stores can leave a stale projection that a later
refresh must repair.

## 4. Runtime execution lifecycle

### 4.1 Launch

The current Streamlit launch bridge is asynchronous:

1. A user explicitly selects or enters work and requests launch.
2. `launch.py` resolves the workspace using task, project default, then repository fallback
   precedence. It resolves the expected branch and performs read-only Git validation.
3. Errors block launch. Dirty-tree, detached-HEAD, and branch-mismatch warnings require explicit
   acknowledgement.
4. The confirmation surface shows the exact project, repository/worktree, branch, task type,
   prompt, and timeout.
5. `launch_service.execute_agent_launch_v2` rejects another active run for the same task or
   resolved workspace before mutating the task.
6. `ExecutionCenterAPI.start_run` persists the runtime task/session/run and asks the Supervisor to
   spawn it.
7. The UI returns immediately to the live Execution Center and polls persisted state.

All normal launch surfaces require an explicit button action. There is no general autonomous task
scheduler on `main`.

### 4.2 Supervisor lifecycle

The Supervisor:

- constructs a fixed Claude CLI argument list and launches with
  `Popen(shell=False, start_new_session=True)`;
- records PID and process-identity evidence in SQLite;
- retains the live `Popen`, pipes, reader threads, watchdog, and cancellation state in memory;
- streams bounded stdout/stderr chunks into run events;
- detects first output and process startup failures;
- enforces timeout;
- cancels the process group with terminate-then-kill escalation;
- classifies exit, cancellation, timeout, spawn, and supervision failures;
- persists final run/session state and report metadata;
- triggers task and completion reconciliation.

The Supervisor owns live subprocess handles only within the hosting Python process. Persisted state
survives a Streamlit/Python restart, but pipes, reader threads, waitable child ownership, and live
`Popen` handles cannot be restored. Startup reconciliation checks PID liveness and recorded process
identity to avoid confusing PID reuse with the original process, then updates persisted state. It
does not reattach output streams or seamlessly resume supervision.

This is process-local durability, not distributed execution or remote-worker ownership.

### 4.3 Cancellation and timeouts

User cancellation is an explicit confirmed action. The Supervisor targets the spawned process
group, sends a graceful termination signal, waits a bounded interval, and escalates to kill if
needed. Timeout uses the same ownership and classification machinery. State-transition guards and
compare-and-swap updates prevent late worker threads from overwriting a newer terminal decision.

### 4.4 Legacy execution

`execute_agent_launch` and `agent_runner.py` retain the synchronous Claude CLI path and
`data/runs.jsonl` persistence; generated-task scripts also remain legacy. The active
`data/chats.json` and `data/activity.jsonl` application stores remain separate from SQLite. These
stores coexist with, rather than replace, the asynchronous SQLite runtime. New Streamlit task
launches use the v2 asynchronous bridge.

## 5. Execution queue

`execution_queue.py` persists a separate queue in `data/execution_queue.json`. Entries carry task
links, dependency state, readiness, launch state, and runtime run references.

Readiness is recalculated when queue checkpoints or UI refreshes occur. A ready entry is a
recommendation to the operator, not scheduled work. Launch still requires an explicit action and
the same repository validation and confirmation as other launches.

Queue writes use atomic whole-file replacement, but the read-modify-write cycle is not protected
by the cross-process lock used for `tasks.json`. Simultaneous writers can overwrite one another.

## 6. Run-to-task reconciliation

`runtime/task_sync.py` is the bridge from SQLite execution/completion state to the Kanban model.
It:

- locates the planning task linked to a runtime run;
- applies terminal launch state, report path, timestamps, verdict, and workflow metadata;
- seeds completion only for eligible successful terminal runs;
- projects completion progress and pull-request state;
- marks the planning task completed only after completion evidence reaches the terminal completed
  state.

The implementation uses `tasks_repository.mutate_tasks`, which serializes task-file mutation and
writes atomically. SQLite remains authoritative if the task projection is temporarily stale.

## 7. Completion lifecycle

The completion domain separates "execution finished" from "change integrated." Its persisted
states cover:

1. execution finished;
2. validation pending/running/passed or failed;
3. result evidence valid or requiring attention;
4. pull request preparation/opening;
5. awaiting checks, reviews, or manual merge;
6. optional policy-controlled merge;
7. target-branch verification;
8. completed, retryable, intervention, or recovery states.

`CompletionOrchestrator` resolves task/project policy, runs allowlisted validation commands,
inspects repository state, pushes without force, discovers or creates a PR through `gh`, observes
checks/reviews/mergeability, optionally merges, recovers a closed-unmerged PR only when enabled,
and verifies that the result is reachable from the configured target branch. SQLite events and
attempt counters provide an audit trail; versioned compare-and-swap updates and bounded backoff
support idempotent retry.

Conservative defaults require validation and a pull request, use squash as the merge method, keep
merge manual, and disable PR recovery. Completion autopilot is implemented but opt-in:
`AICC_COMPLETION_AUTOPILOT` is disabled by default. When enabled, the hosting process runs a
background loop that calls `advance_pending`; this is a completion-state worker, not a general
task scheduler.

## 8. Portfolio intelligence and launch flow

### 8.1 Portfolio model and intelligence

`portfolio_models.py` parses flat frontmatter task cards from Portfolio lanes. Card fields include
task/project IDs, status, dependencies, conflicts, branch/worktree overrides, launch and permission
profiles, and planning metadata.

`portfolio_intelligence.py` builds read-only derived views:

- per-project health and counts;
- dependency graph, execution waves, cycles, and critical path;
- ready, blocked, running, and completed classifications;
- capacity and workload summaries;
- deterministic recommendations and parse-quality warnings.

The intelligence layer does not edit cards or schedule launches. The Portfolio Overview panel
renders its snapshot.

### 8.2 Launch orchestration

For a ready Portfolio card:

1. `portfolio_config.py` resolves an explicitly configured local repository mapping.
2. `portfolio_launch.py` validates identity, lane, dependency/conflict state, repository,
   requested/default branch, worktree, launch profile, permission profile, registry state, and
   collisions.
3. The UI displays a dry-run plan.
4. Explicit user confirmation claims the task under a Portfolio lock.
5. `worktree_launcher.py` validates an existing worktree or creates a task branch/worktree from
   the selected base.
6. The orchestration registers the Portfolio launch, adds/links queue state, and calls the same
   asynchronous runtime API.
7. If failure occurs before process ownership transfers, rollback removes only the branch,
   worktree, registry, or queue resources that this transaction created.

Batch launch is also explicit, bounded by a concurrency cap, and preflights task/worktree
collisions. It is orchestration, not autonomous scheduling.

## 9. Git and GitHub privileged boundaries

Git access is not globally read-only. Capabilities are separated:

| Boundary | Capability | Safeguard |
|---|---|---|
| Git Center, repository status, launch preflight | Read-only inspection | Fixed read-only commands |
| Claude review/final-gate/architecture-review task | Read/search only | Claude tool allowlist excludes Bash and edit tools |
| Claude implementation/remediation task | File editing and validation | Git-write commands denied in Claude tool configuration |
| Portfolio worktree launcher | Create/attach branch and worktree | Exact repository validation, collision checks, explicit launch, bounded rollback |
| Completion Git adapter | Push or recreate branch | Fixed argv, no force-push, policy and state-machine gates |
| Completion GitHub adapter | Discover/create/optionally merge PR | Local authenticated `gh`, fixed argv, explicit project policy, manual merge default |
| Validation adapter | Run configured checks | Executable allowlist, `shell=False`, timeout, captured bounded output |

Worktree creation, push, pull-request creation, and merge are privileged write capabilities. Their
availability does not imply blanket authorization. User-triggered launch controls and conservative
completion policies are the safety boundary; enabling an opt-in automatic policy must be treated
as an explicit operational decision.

## 10. Streamlit execution characteristics

Streamlit reruns `app.py` on interaction. `st.cache_resource` preserves the runtime API and its
Supervisor while the server process remains alive; session state preserves browser-session UI
choices. Timed fragments poll SQLite and render live state without making the queue a scheduler.

This arrangement has two implications:

- the UI and service layer share one Python host instead of communicating with an independent
  daemon;
- server restart recreates services from persisted state but loses process-local handles.

The application is local-first, but "local" describes deployment intent, not an enforced network
bind.

## 11. Security and sensitivity

Project configuration marks sensitive projects such as BANK and LEGAL. UI flows warn before
launch or chat, avoid automatic context attachment, and sanitize cross-project Workspace Home
snapshots before rendering.

Prompts and reports can contain sensitive content. Runtime databases, JSON/JSONL stores, reports,
Portfolio mappings, and generated artifacts are machine-local operational data and must not be
committed. Subprocess adapters avoid shell interpolation and bound execution time, but the
application still launches tools with the local user's filesystem and credential context.

## 12. Validation and quality gates

The supported local gates are:

```bash
python -m compileall -q command_center app.py
ruff check .
pytest -q
```

The test suite covers storage isolation, SQLite migrations and concurrency, API/state transitions,
real fake-process supervision, process-tree cancellation and timeout, restart reconciliation,
completion scenarios and races, Git/GitHub adapters, queue behavior, worktree creation and
rollback, Portfolio parsing/launch/intelligence, read-model redaction, and Streamlit rendering.

`requirements-dev.txt` declares pytest but not Ruff. No MyPy or other type checker is configured.
There is no checked-in mandatory CI workflow, so local gates are conventions rather than enforced
repository checks.

## 13. Current risks and boundaries

- Multiple persistence authorities require reconciliation and cannot commit atomically together.
- The legacy synchronous `runs.jsonl` path and current asynchronous SQLite execution path coexist;
  active chat and activity stores remain separate from both.
- Live Supervisor ownership is confined to one Python process.
- `app.py` and several runtime and Portfolio modules are large, concentrated change surfaces.
- Toolchain declarations are incomplete and no mandatory CI is checked in.
- Execution queue read-modify-write operations are not cross-process locked.
- Streamlit can be exposed to the network unless it is explicitly bound to localhost.
- There is no production-readiness guarantee, distributed execution, durable remote-worker
  ownership, or seamless process resumption.

## 14. Planned desktop architecture

The accepted desktop documents propose a PySide6/Qt Widgets client that reuses plain-Python domain
and runtime services through application adapters. That target is deliberately outside the current
runtime:

- no `command_center.desktop` package exists;
- PySide6 is not a runtime dependency;
- no desktop executable or installer is built;
- no native packaging or update channel is implemented.

See [`docs/desktop/README.md`](docs/desktop/README.md) for the design status. Roadmap statements in
that directory must not be read as current capability.

## 15. Related decisions and operator documentation

- [`docs/adr/0001-engineering-control-center-v2-increment-1.md`](docs/adr/0001-engineering-control-center-v2-increment-1.md)
- [`docs/adr/0002-project-config-as-canonical-engineering-defaults.md`](docs/adr/0002-project-config-as-canonical-engineering-defaults.md)
- [`docs/adr/0003-live-execution-center-v2-and-kanban-launch-bridge.md`](docs/adr/0003-live-execution-center-v2-and-kanban-launch-bridge.md)
- [`docs/adr/0004-autonomous-task-completion-pipeline.md`](docs/adr/0004-autonomous-task-completion-pipeline.md)
- [`docs/completion-pipeline.md`](docs/completion-pipeline.md)
- [`CURRENT_STATE.md`](CURRENT_STATE.md)
