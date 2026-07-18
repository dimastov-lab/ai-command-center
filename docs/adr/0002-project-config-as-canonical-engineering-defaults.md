# ADR 0002 — Project Config as the Canonical Source of Engineering Defaults

Status: **Accepted, implemented.**

## Context

Launch Manager already resolves a task's workspace correctly, in three
tiers (`command_center.launch.resolve_workspace_path`, unchanged since ADR
0001):

```
task.workspace_path → project.default_workspace_path → project.repository_path
```

This was already correct and already tested (`tests/test_launch.py`). The
actual gap was upstream of Launch: the Project model had no place to declare
`default_branch`, `default_executor`, or `default_prompt`, and Task Creation
never populated `workspace_path`/`branch`/`executor`/`prompt` on a new task —
it relied entirely on Launch's runtime fallback, which only reaches
`project.default_workspace_path` if that field happens to be configured. For
a project where only `repository_path` was ever set (e.g. AIOS), every task
silently fell all the way through to the primary repository instead of an
intended development worktree — an architectural gap in Project Config
ownership, not a bug in Launch itself.

## Decision

### 1. Project Config, not Launch, owns every engineering-environment default

`command_center/project_config.py`'s `default_project_config` is extended
with `default_branch`, `default_executor`, `default_prompt`, plus the
descriptive/planning fields the mission's Project Model requires
(`description`, `status`, `priority`, `progress`, `current_sprint`,
`current_milestone`, `owner`). All are optional overrides layered onto the
built-in defaults by `load_project_configs`, persisted in the existing
`data/project_config.json` via a new generic setter, `save_project_settings`.
Launch itself gained no new configuration surface — it already consumes
`project_config` dicts by key, so new keys are simply available to it without
any change to `launch.py`.

### 2. `repository_path` vs. `default_workspace_path` remain distinct fields

`repository_path` is the project's canonical checkout — the fallback tier
every pre-existing task already relied on. `default_workspace_path` is an
explicit, optional override for a *different* engineering environment (a
worktree, a fork, a sandboxed checkout) that Launch prefers when set. This
increment does not merge or rename either field; it only makes sure a new
task actually inherits whichever one is authoritative for its project,
instead of leaving both unset and hoping Launch's fallback chain reaches the
right one.

### 3. Task inheritance is materialized at creation time, not deferred to Launch

`project_config.task_defaults_from_project(cfg)` computes
`{workspace_path, branch, executor, prompt}` for a project, with
`workspace_path` mirroring Launch's own precedence (`default_workspace_path`
else `repository_path`). The Create Task page (`app.py`) calls this the
moment a project is selected — outside `st.form`, so the read-only preview
reacts immediately — and hands the result to
`tasks_repository.new_task_record`'s new optional kwargs. The task record
that lands in `tasks.json` is therefore self-describing: inspecting the task
alone (without cross-referencing the project config at read time) tells you
which workspace/branch/executor/prompt it will run against. This is a
deliberate choice over resolving these fields lazily at Launch time only —
materializing them earlier means legacy-created tasks and freshly-created
tasks are handled by exactly one inheritance path, and the value baked onto
the task is provably identical to what Launch would independently resolve
(covered by `tests/test_launch.py`'s
`test_launch_resolves_the_same_workspace_a_task_inherited_at_creation`).

### 4. Explicit task-level overrides always win

The Create Task UI's "override" expander accepts a per-field override for
workspace/branch/executor/prompt; a non-empty override always takes
precedence over the inherited project default when the task record is
built. This mirrors the pre-existing precedence philosophy in
`resolve_workspace_path` (an explicit, present value always wins over a
lower-precedence default) — applied one layer earlier, at task creation
rather than at launch resolution.

### 5. Launch Manager remains independent and unmodified

No line in `command_center/launch.py` or `command_center/launch_service.py`
changed. `resolve_workspace_path`'s three-tier precedence is exactly what it
was before this increment. Task inheritance is additive plumbing that feeds
Launch better-populated tasks; Launch does not know or care whether a task's
`workspace_path` was hand-typed, inherited at creation, or left over from a
pre-inheritance task record — its contract with `task`/`project_config`
dicts is unchanged.

### 6. Validation is advisory, never blocking

`project_config.validate_project_settings(cfg)` returns a plain list of
warning strings — repository/workspace path existence, workspace-is-a-
git-repo, configured branch exists, configured executor is known/available,
prompt length sanity — and never raises or blocks a save. This matches the
tolerance the pre-existing `repository_path` field already had (an invalid
path was always only caught later, by Launch's own `validate_launch`), so a
project can be configured ahead of the workspace actually existing on a
given machine (e.g. before a worktree is checked out) without the settings
UI refusing to save.

### 7. Backward compatibility: no migration, normalize on load

`load_project_configs` merges any subset of the new fields present in
`project_config.json` on top of `default_project_config`'s defaults, field by
field. A `project_config.json` written before this increment — containing
only `repository_path` (or `repository_path` + `default_workspace_path`, per
ADR 0001's addition) — continues to load unchanged, with every new field
silently taking its built-in default. No migration script, no schema
version bump, no rewrite of existing config files. Symmetrically, a task
created before this increment (missing `workspace_path`/`branch`/`executor`/
`prompt` entirely, or holding the pre-existing `None`/`""` defaults) is
untouched by this change — `tasks_repository.new_task_record`'s new kwargs
default to `None`, which preserves the exact pre-existing record shape when
omitted, and `models.normalize_task_execution` (unmodified) continues to
backfill legacy task dicts on load exactly as before.

## Consequences

- **Positive**: a task's engineering environment is no longer implicit in
  "whatever Launch happens to resolve at run time" — it is visible,
  editable, and overridable at the point the task is created.
- **Positive**: zero blast radius on Launch — every existing Launch test
  (`tests/test_launch.py`, `tests/test_launch_service.py`,
  `tests/test_app_streamlit.py`'s launch-flow tests) passes unmodified,
  because `resolve_workspace_path` and the launch pipeline were not touched.
- **Trade-off accepted**: `save_project_settings` is a generic
  `**fields`-based setter distinct from the pre-existing single-purpose
  `save_repository_path`. The two coexist rather than being unified, to
  avoid touching `save_repository_path`'s existing call sites and tests —
  a deliberate minimal-diff choice, not an oversight.

## Known limitation (out of scope for this increment)

**Arbitrary project creation/renaming is not supported.** `models.PROJECT_IDS`
remains a fixed, hardcoded list (`AIOS`, `AICOS`, `BANK`, `LEGAL`, `BUSINESS`,
`PERSONAL`); `display_name` ("Project Name" in the mission's Project Model)
exists but is not user-editable, and there is no UI or service path to add,
remove, or rename a project. Supporting that would mean replacing the
hardcoded `PROJECT_IDS` list with a persisted project registry — a
materially larger change touching every module that iterates
`models.PROJECT_IDS` today (`project_config`, `tasks_repository`'s callers in
`app.py`, the dashboard, the recommender). This increment only extends the
*configuration* owned by each of the six existing projects; it does not
change how many projects exist or how they are identified.

## Verification

`ruff check .`, `python -m compileall -q .`, `pytest -q` (526 passed, up
from 496 pre-increment), `git diff --check` — all clean. `AppTest` coverage
added for the Create Task inheritance/override flow and the Projects
settings save/validate flow, exercising real form submission against real
`tasks.json`/`project_config.json` round-trips.
