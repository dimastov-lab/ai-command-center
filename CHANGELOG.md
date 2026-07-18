# Changelog

All notable changes to AI Command Center are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does not yet follow strict semantic versioning tags in Git; versions below refer to
functional application milestones of `app.py`.

## [Unreleased] — Desktop Architecture D0

### Added
- **`docs/desktop/`**: canonical, implementation-ready documentation set for a native
  PySide6/Qt Widgets desktop application — product vision, target architecture, information
  architecture, design directions (Professional Control Plane approved), design system, a
  Workspace Home native-page spec built on the existing `build_workspace_home_snapshot` read
  model, macOS/Windows platform behavior, frozen Desktop Increment 1 (D1–D4) scope, and a
  commit-sized implementation roadmap. Documentation only — no desktop code, dependencies, or
  packaging exist yet. Next implementation stage: D1A.

## [Unreleased] — Sprint 3 Increment 1: Workspace Home

Implements `WORKSPACE_HOME_ARCHITECTURE.md` in full (all 10 steps of §17's implementation
plan). That document's own status header ("architecture only, no code changed") is now stale —
the design is implemented, not just approved.

### Added
- **`command_center/git_info.py`**: per-project git/worktree discovery (`get_status`,
  `get_worktrees`, `get_log`, `get_diff_stat`, `get_branches`, `get_remotes`), extracted from
  `app.py`'s original ROOT-only helpers and parameterized by `cwd: Path`. `app.py`'s Git Center
  and Workspace Launcher pages are now thin wrappers over it (zero behavior change).
- **`command_center/artifacts.py`**: `list_markdown_files`, `project_from_path`,
  `infer_task_type_from_filename`, `read_text` — extracted verbatim from `app.py`, Streamlit-free,
  a leaf module. Every existing `app.py` call site repointed at it.
- **`db.list_runs`/`ExecutionCenterAPI.list_runs`** gained `states` (plural, `IN (...)`) and
  `limit` (SQL `LIMIT`) parameters, additive and backward compatible; `state`+`states` together
  raise `ValueError`. `EXECUTION_CENTER_ACTIVE_STATES` moved to `runtime/db.py` beside
  `TERMINAL_STATES`.
- **`command_center/workspace_home.py`**: the Workspace Home read model
  (`build_workspace_home_snapshot`) and its sensitivity redaction stage
  (`sanitize_workspace_project_entry`) — cross-project rollup of projects, git worktrees, active/
  recent runs (v1.2 + v2, merged and source-tagged), reports, artifacts, and activity, with every
  BANK/LEGAL entry passed through a field allowlist *before* it reaches the renderer.
- **Workspace Home page** (`workspace_home` nav entry): a new, additional page — Dashboard and
  Workspace Launcher are unchanged. Read-only; every Quick Action (Open Project, New Task, Launch
  Run, view Run/Report/Artifact) delegates to the existing gated forms, never mutates directly.
- Tests: `test_git_info.py`, `test_artifacts.py`, `test_workspace_home.py`,
  `test_workspace_home_ui.py`, plus extensions to `test_runtime_db.py`/`test_runtime_api.py` —
  389 tests total (up from 333), including a dual-layer (snapshot + rendered-page) regression
  test that no BANK/LEGAL prompt/log/report-body/raw-path content ever reaches the page.

### Deviation from the architecture document
- §4's data-source map lists `load_tasks()` (the v1.2 Kanban store, which lives only in `app.py`)
  as a Projects-section input. `workspace_home.py` cannot import `app.py` under any circumstance
  (§6/§9.2, a hard constraint stated three times in the document) and `load_tasks` was not in
  Condition 4's extraction scope, so the per-project task count instead uses
  `ExecutionCenterAPI.list_tasks(project=...)` (v2 SQLite tasks, an explicitly allowed read
  method). This counts v2 orchestration tasks, not v1.2 Kanban cards — recorded in
  `workspace_home.py`'s module docstring.

## [1.2.0] - 2026-07-16

### Added
- **`command_center/` package**: `models`, `storage`, `project_config`, `agent_runner`,
  `report_parser`, `chat_service`, `workflow`, `activity_log` — see ARCHITECTURE.md §11.
- **Project Chat** (`chat` page): per-project conversations with a provider abstraction (local
  manual mode, Claude Code CLI, optional OpenAI Responses API gated on `OPENAI_API_KEY` +
  `OPENAI_MODEL`); save any message into `reports/`, or convert it into a task.
- **Claude Code runner**: launch Claude Code from a Kanban task, the Agents page, Project Chat, or a
  generated-task preview, with an explicit repository/branch/agent/prompt confirmation step, a
  synchronous timeout-bounded execution, and full stdout/stderr capture.
- **Full report storage**: every completed run's untruncated report is saved under
  `reports/<PROJECT>/`.
- **Structured result extraction** (`report_parser.py`): deterministic verdict/findings/files/
  commit/branch/PR/validation/git-status/next-action parsing with evidence, a confidence level, and
  a manual-correction UI that never discards the original extraction.
- **Create Next Task**: verdict-driven task-type/workflow-stage/objective suggestion on a completed
  run, always requiring review before creating anything and never auto-executing.
- **Run journal** (`runs` page): filterable list of every run plus a full detail view; Executive
  Dashboard gained run metrics (today's runs, success/failure, awaiting remediation/final review,
  approved-for-commit, average duration by agent, open Blocker/High findings).
- **Task workflow fields**: `parent_task_id`, `prior_run_id`, `current_run_id`, `workflow_stage`,
  `latest_verdict`, `report_path`, `repository_path`, `branch`, `agent`, `last_run_at` — additive,
  backfilled on load, parallel to (not a replacement for) the existing Kanban `status`.
- **Project repository configuration**: Projects → "Настройки репозитория" tab; local overrides in
  gitignored `data/project_config.json`; no path ever guessed (only ever a verified-existing git
  repo, shown as a suggestion the user must save).
- **Sensitive-project handling**: BANK/LEGAL show an explicit warning before any agent launch or
  chat call and never auto-attach context files.
- `AICOS` added to the project registry (repository path unconfigured — no known local path).
- `requirements-dev.txt` (adds `pytest`), `.env.example`, and a `tests/` suite (pytest +
  Streamlit `AppTest`) covering storage, migration, path validation, the report parser, next-task
  mapping, report persistence, run filtering, sensitive-project warnings, and refusal to run
  against unconfigured paths or via a shell.

### Changed
- `data/runs.jsonl` and `data/activity.jsonl` use JSON Lines instead of a single JSON array — see
  ARCHITECTURE.md §11.2 for why. `reports/` is now gitignored (may contain BANK/LEGAL content).

### Security
- The Claude Code runner never calls git-write subcommands itself, and refuses to run against any
  repository path not present in project configuration.
- Read-only task types (`review`/`final_gate`/`architecture_review`) run with the model's tool set
  restricted to `Read,Grep,Glob` via `--tools` — `Bash` and every file-edit tool are entirely absent
  from that run, not merely pattern-denied. Implementation/remediation task types keep `Bash` but
  have the specific git-write subcommands denied via `--disallowedTools` — see ARCHITECTURE.md §11.3
  for exactly what each task-type class does and does not enforce.
- Fixed during independent review (F-01/F-02): an earlier version of this control denied specific
  `Bash(git ...)` patterns for read-only task types while leaving the general-purpose `Bash` tool
  available, which left `git apply`/`checkout`/`stash` and plain shell writes unrestricted for task
  types documented as unable to modify any file. Replaced with the `--tools` allowlist above.

## [1.1.0] - 2026-07-15

### Added
- Executive Dashboard: cross-project rollup (totals, active/blocked/completed, workload estimate),
  per-project status parsed from `CURRENT_STATE.md`, priority breakdown chart, workload by owner.
- Command Palette (`Mod+K`): searchable dialog to jump to any page or start a task for a project.
- Focus Mode: single-task distraction-reduced view with a quick status/"mark done" control.
- Timeline: unified, day-grouped, project-filterable feed of task events and file activity.
- AI Agents page: catalog of the task types supported by `scripts/start-task.sh`, with execution
  rules, live usage stats, and a shortcut into the task creator.
- Smart Tasks: task records gained `priority`, `owner`, `estimate_hours`, and `depends_on`;
  Kanban cards show priority/owner/estimate badges and a "Заблокировано" (blocked) badge for
  tasks with unmet dependencies; Kanban gained a priority filter.
- Git Center: expanded read-only Git view with commit history, full changed-file list,
  `git diff --stat` (staged/unstaged), branches, and remotes.
- Workspace Launcher: `git worktree list` overview plus per-project quick-jump cards (in-app
  navigation and copyable file paths).

### Changed
- `data/tasks.json` records are now backfilled with default Smart Tasks fields on load, so task
  files created before this release keep working without migration.
- The former "Git и активность" page was split: Git-only content moved to the new **Git Center**
  page, and the activity log moved to the new **Timeline** page.
- `scripts/start-ui.sh` now forwards its arguments to `streamlit run` (e.g. `--server.port`)
  instead of silently dropping them.

### Fixed
- Cross-page navigation actions (command palette, AI Agents shortcuts, Workspace Launcher,
  Focus Mode exit) no longer raise `StreamlitAPIException` when triggered — navigation targets
  are now staged in `pending_*` session-state keys and applied before the sidebar navigation
  widget is instantiated on the next run, instead of writing directly to an already-instantiated
  widget's key.

## [1.0.0] - 2026-07-15

### Added
- Initial working Streamlit application (`app.py`) launched via `python -m streamlit run app.py`.
- Dashboard: project/task counts, generated/report file counts, latest activity, active tasks
  grouped by project.
- Task creator: form (project, task type, objective, Kanban status) that runs
  `scripts/start-task.sh` as a subprocess (no `shell=True`, fixed argument list, 30s timeout,
  captured stdout/stderr) and records a matching task.
- Kanban board: Backlog / Next / In Progress / Review / Done columns, project filter, status
  change via dropdown, delete, and a task-details expander. Persisted to `data/tasks.json` with
  atomic writes.
- Project browser: per-project status, generated tasks, reports, and context, each with file
  modification time.
- Generated tasks browser and Reports browser: recursive, project-filterable, newest-first,
  markdown preview.
- Global context view: `CURRENT_STATE.md`, `DECISIONS.md`, `INBOX.md`.
- Git status: read-only branch/dirty/modified/untracked/last-commit summary.
- `requirements.txt` and `scripts/start-ui.sh` for one-command startup.
