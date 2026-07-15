# Changelog

All notable changes to AI Command Center are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does not yet follow strict semantic versioning tags in Git; versions below refer to
functional application milestones of `app.py`.

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
