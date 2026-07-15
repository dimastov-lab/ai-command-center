# AI Command Center

Local control center for managing the AIOS, BANK, LEGAL, BUSINESS and PERSONAL projects: task
creation, a Kanban board, generated AI task files, reports, and repository/context status — all
from one Streamlit application running on your machine.

## Getting started

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the application

```bash
python -m streamlit run app.py
```

### One-command startup

Once the virtual environment exists and dependencies are installed, you can launch the app with:

```bash
scripts/start-ui.sh
```

The script resolves the repository root, activates `.venv` if present, verifies Streamlit is
available, and starts the app. It does not create the virtual environment or install packages —
run steps 1 and 2 first.

## Task storage

Kanban tasks are stored locally as JSON at `data/tasks.json` (created automatically on first
run). Writes are atomic (write to a temp file, then replace). This file is local application
state, separate from the AI task files generated under `generated/<PROJECT>/` by
`scripts/start-task.sh`. Older task records without the Sprint 2 fields (see below) are
backfilled with defaults automatically when loaded, so existing data keeps working.

## Sprint 2 (v1.1) features

- **Executive Dashboard** — cross-project rollup: total/active/blocked/completed tasks, workload
  estimate, per-project status (parsed from `CURRENT_STATE.md`), priority breakdown chart, and
  workload by owner.
- **Command Palette (`Mod+K`)** — press `Ctrl+K` / `Cmd+K` (or click the sidebar button) to open a
  searchable dialog that jumps to any page or starts a new task for a given project.
- **Focus Mode** — a distraction-reduced page that shows a single active task at a time (with a
  quick status/"mark done" control) and collapses the sidebar while active.
- **Timeline** — a unified, day-grouped, newest-first feed combining task creation/status events
  with generated/report file activity, filterable by project.
- **AI Agents** — a catalog of the task types supported by `scripts/start-task.sh` (implementation,
  review, remediation, final_gate, architecture_review), each showing its execution rules and
  live usage stats, with a one-click shortcut into the task creator.
- **Smart Tasks** — Kanban tasks now carry priority, owner, a time estimate, and dependencies on
  other tasks; a task with unmet dependencies is flagged "Заблокировано" on its card and rolled
  up on the Executive Dashboard.
- **Git Center** — expands the read-only Git view with commit history, the full changed-file
  list, `git diff --stat` (staged/unstaged), branches, and remotes. Still no mutating Git actions.
- **Workspace Launcher** — lists `git worktree` entries and, per project, the exact status/context
  file paths plus one-click in-app shortcuts to open that project or start a task for it.

## Scope and limitations

- Local-only tool: no authentication, no network services, no database.
- Read-only Git everywhere (status, log, diff, branches, remotes, worktrees): the UI never runs
  commit, push, pull, merge, reset, rebase, or checkout.
- All subprocess calls use fixed argument lists (never `shell=True`), short timeouts, and captured
  stdout/stderr — limited to `scripts/start-task.sh` and read-only `git` subcommands.
- Workspace Launcher does not spawn external editors/file managers (out of scope for the allowed
  subprocess surface); it navigates within the app and shows copyable local paths instead.
- Focus Mode's sidebar auto-collapse relies on Streamlit's `initial_sidebar_state`, which is a
  best-effort, browser-session-scoped hint rather than a guaranteed collapse on every rerun.
- `scripts/start-task.sh` currently only recognizes the `AIOS`, `BANK`, and `LEGAL` projects;
  creating a task for `BUSINESS` or `PERSONAL` will surface the script's error in the UI.
- Only `AIOS`, `BANK`, and `LEGAL` have dedicated context files under `context/`; `BUSINESS` and
  `PERSONAL` are handled gracefully with an informational message.
- No drag-and-drop on the Kanban board; moving a task between columns is done via a dropdown.
