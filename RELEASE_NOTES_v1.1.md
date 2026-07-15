# AI Command Center v1.1 — Release Notes

**Date:** 2026-07-15
**Scope:** Sprint 2 — Executive Dashboard, Command Palette, Focus Mode, Timeline, AI Agents,
Smart Tasks, Git Center, Workspace Launcher.

## Highlights

- **Executive Dashboard** — a program-level view: total/active/blocked/completed task counts,
  estimated active workload, per-project status (parsed from `CURRENT_STATE.md`), a priority
  breakdown chart, workload by owner, and a list of blocked tasks with their unmet dependencies.
- **Command Palette** — press `Ctrl+K` / `Cmd+K` (or click the sidebar button) to search and jump
  to any page, or jump straight into task creation for a chosen project.
- **Focus Mode** — a single-task, minimal-chrome view with a status control and a one-click
  "mark done" action; the sidebar collapses while active.
- **Timeline** — a day-grouped, newest-first feed merging task lifecycle events (created/status
  changes) with file activity from `generated/`, `reports/`, `projects/`, and `context/`.
- **AI Agents** — a catalog of the five task types `scripts/start-task.sh` supports, each with its
  execution rules, live usage counts, and a shortcut into the task creator.
- **Smart Tasks** — Kanban tasks now carry priority, owner, an hour estimate, and dependencies on
  other tasks. A task blocked on an unfinished dependency is flagged on its card and rolled up on
  the Executive Dashboard.
- **Git Center** — the read-only Git view now includes commit history, the full changed-file
  list, staged/unstaged `git diff --stat`, branches, and remotes.
- **Workspace Launcher** — a `git worktree list` overview plus, per project, the exact status and
  context file paths and one-click in-app shortcuts to open a project or start a task for it.

## Compatibility

- Existing `data/tasks.json` files from v1.0 load without modification: missing Smart Tasks
  fields (`priority`, `owner`, `estimate_hours`, `depends_on`) are backfilled with defaults
  (`Medium`, `""`, `0.0`, `[]`) the first time each task is loaded.
- All v1.0 pages and workflows (Dashboard, task creation, Kanban, Project browser, Generated
  tasks, Reports, Global context) are unchanged in behavior.
- The former single "Git и активность" page is now two pages — **Git Center** (Git only) and
  **Timeline** (activity feed) — reachable from the sidebar as before, just split by concern.

## Architecture

No architectural changes. The application remains a single Streamlit entry point (`app.py`),
using `pathlib` for file access, JSON for local task persistence, and `subprocess` calls limited
to `scripts/start-task.sh` and read-only `git` subcommands (never `shell=True`, always with fixed
argument lists and timeouts). See `ARCHITECTURE.md` for the full description.

## Fixed in finalization

- `st.session_state.nav_page` (and related widget keys) could not be reassigned from inside the
  command palette, AI Agents, Workspace Launcher, or Focus Mode exit action once the sidebar
  navigation widget had already run in that script pass — Streamlit raised
  `StreamlitAPIException`. Fixed by staging the target page/selection under `pending_*` session
  keys, applied once at the top of the script before any matching widget is created.
- `scripts/start-ui.sh` did not forward its own command-line arguments to `streamlit run`, so
  flags such as `--server.port` were silently ignored. Fixed by forwarding `"$@"`.
- Minor Ruff findings cleaned up: a dict comprehension over a fixed key list replaced with
  `dict.fromkeys(...)`, and `zip()` over two known-equal-length sequences made explicit with
  `strict=True`.

## Validation performed

- `ruff check app.py` — clean (default rule set, and an extended pass with `F,B,SIM,C4,ARG`).
- `python -m py_compile app.py` and an `ast.parse` pass — clean.
- Manual unused-import / unused-function audit — none found.
- `bash -n` on both shell scripts — clean; `scripts/start-task.sh` and `scripts/start-ui.sh`
  confirmed executable.
- A live Streamlit server smoke test (`/_stcore/health` → HTTP 200) plus a `streamlit.testing.v1`
  `AppTest` regression pass exercising all 13 pages and the key interactive flows (command palette
  navigation and quick-create, Agents/Workspace/Focus navigation shortcuts, and Smart Task
  creation with dependencies producing the correct "Заблокировано" badge and Executive Dashboard
  blocked count) — all passing, no exceptions.

## Known limitations (carried over / new)

- `scripts/start-task.sh` only recognizes `AIOS`, `BANK`, `LEGAL`; creating a task for `BUSINESS`
  or `PERSONAL` surfaces the script's own error in the UI (unchanged from v1.0).
- Workspace Launcher does not spawn external editors or file managers — it stays within the
  allowed subprocess surface (`scripts/start-task.sh` and read-only `git`) and instead navigates
  in-app and shows copyable local paths.
- Focus Mode's sidebar auto-collapse uses Streamlit's `initial_sidebar_state`, a best-effort,
  per-browser-session hint rather than a guaranteed collapse on every rerun.
- No drag-and-drop on the Kanban board; status changes are dropdown-based.

## Upgrade steps

No action required. Pull the changes, keep using the same `.venv` and `data/tasks.json` —
`pip install -r requirements.txt` again only if `requirements.txt` itself changed (it did not in
this release).
