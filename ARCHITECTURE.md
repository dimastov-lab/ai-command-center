# AI Command Center — Architecture

This document describes how the application is built as of **v1.1**. It reflects the code in
`app.py`, not aspirations — if this file and the code disagree, the code is authoritative.

## 1. Shape of the system

AI Command Center is a **single-process, single-file, local-only Streamlit application**. There
is no backend service, no database, and no network dependency beyond what `pip install streamlit`
pulls in.

```
Browser (Streamlit client)
        │  HTTP / WebSocket, localhost only
        ▼
Streamlit server  ──  app.py  (re-executed top-to-bottom on every interaction)
        │                                   │
        ├── reads/writes ──────────────────►│ data/tasks.json          (Kanban task store)
        ├── reads ──────────────────────────►│ projects/*.md            (project status)
        ├── reads ──────────────────────────►│ context/*_CONTEXT.md     (project context)
        ├── reads ──────────────────────────►│ generated/<PROJECT>/*.md (AI task files)
        ├── reads ──────────────────────────►│ reports/<PROJECT>/*.md   (AI report files)
        ├── reads ──────────────────────────►│ CURRENT_STATE.md, DECISIONS.md, INBOX.md
        ├── subprocess ─────────────────────►│ scripts/start-task.sh    (fixed args, timeout)
        └── subprocess ─────────────────────►│ git <read-only subcommand>
```

Everything the app knows comes from the filesystem under the repository root, plus
`data/tasks.json`. There is no hidden state anywhere else.

## 2. Execution model

Streamlit re-runs `app.py` from top to bottom on every user interaction (widget change, button
click, `st.rerun()`). `app.py` is intentionally kept as a **direct script**, not wrapped in a
`main()` function, per the project's Streamlit conventions — this is idiomatic for Streamlit and
keeps the top-to-bottom re-run model visible in the code itself.

A single run does, in order:

1. Resolve any staged cross-page navigation (`pending_*` session-state keys — see §5).
2. `st.set_page_config(...)`, with the sidebar defaulting to collapsed only when Focus Mode
   (`nav_page == "focus"`) is the active page.
3. Render the title/caption and the sidebar (command-palette trigger button + page navigation
   radio + footer captions).
4. `tasks = load_tasks()` — read and normalize `data/tasks.json` for this run.
5. Render the command palette dialog if it has been opened.
6. Dispatch to exactly one page section via an `if page_key == "...": ... elif ...` chain.

There is no routing framework and no multi-page-app folder (`app_pages/`) — a single flat
`if/elif` chain keyed by `page_key` (the sidebar radio's value) implements all 13 pages. This was
a deliberate choice to preserve the original single-entry-point architecture rather than introduce
Streamlit's `st.navigation`/`st.Page` machinery.

## 3. Module layout (inside `app.py`)

The file is organized into clearly delimited sections, in this order:

| Section | Contents |
|---|---|
| Constants | `ROOT` and derived paths, `PROJECTS`, `CONTEXT_FILES`, `TASK_TYPES` (+ Russian labels), `AGENT_ROLES`, `KANBAN_COLUMNS`, `PRIORITIES` (+ colors), `GLOBAL_FILES`, `NAV` |
| File and text helpers | `read_text`, `format_mtime`, `format_estimate`, `list_markdown_files`, `project_from_path`, `infer_task_type_from_filename`, `gather_activity`, `parse_project_statuses` |
| Task persistence | `normalize_task`, `load_tasks`, `save_tasks`, `new_task_record`, `update_task_status`, `delete_task`, `task_label`, `unmet_dependencies`, `is_blocked` |
| Task generation | `run_start_task_script` |
| Git (read-only) | `run_git_command`, `get_git_status`, `get_git_log`, `get_git_diff_stat`, `get_git_branches`, `get_git_remotes`, `get_git_worktrees` |
| Timeline | `build_timeline_events` |
| Page setup | pending-navigation resolution, `st.set_page_config`, sidebar, command palette |
| 13 page sections | one `if`/`elif` block per `NAV` entry (below) |

There is exactly one Python file. No `pages/` or `app_pages/` directory, no shared module, no
package. This matches the original architecture and the project's "keep it simple, local, single
entry point" constraint — splitting into modules was considered but rejected to avoid redesigning
a working, moderately-sized (≈1350 line) script for no functional benefit.

## 4. Pages

Each `NAV` entry is `key -> (label, material_icon)`; the sidebar `st.radio(key="nav_page")`
drives a flat dispatch:

| Key | Page | Purpose |
|---|---|---|
| `dashboard` | Обзор | Operational KPIs, active tasks by project, recent activity |
| `executive` | Исполнительная панель | Cross-project rollup, blocked tasks, priority/owner breakdowns |
| `create` | Создать задачу | Task-creation form → `scripts/start-task.sh` → Kanban record |
| `kanban` | Kanban | 5-column board with priority/owner/estimate badges and dependency blocking |
| `agents` | AI-агенты | Catalog of the 5 task types, their rules, and usage stats |
| `timeline` | Таймлайн | Day-grouped feed of task events + file activity |
| `projects` | Проекты | Per-project status/generated/reports/context browser |
| `generated` | Сгенерированные задачи | Global, recursive, project-filterable browser of `generated/` |
| `reports` | Отчёты | Global, recursive, project-filterable browser of `reports/` |
| `context` | Глобальный контекст | `CURRENT_STATE.md`, `DECISIONS.md`, `INBOX.md` |
| `git_center` | Git Center | Read-only branch/status/log/diff/branches/remotes |
| `workspace` | Workspace Launcher | `git worktree list` + per-project quick-jump cards |
| `focus` | Focus Mode | Single-task, minimal-chrome working view |

## 5. State management

Two kinds of state exist:

- **Persistent state**: `data/tasks.json`, written atomically (`tempfile.mkstemp` in the same
  directory + `os.replace`) so a crash mid-write cannot corrupt the file. Every task record is
  passed through `normalize_task` on load, so records written by an older version of the app
  (missing `priority`/`owner`/`estimate_hours`/`depends_on`) are backfilled with defaults rather
  than breaking newer code.
- **Session state** (`st.session_state`), in-memory per browser session, used for:
  - Widget values (`nav_page`, all the `*_select`/`*_filter` keys, form fields).
  - `show_command_palette` — whether the command-palette dialog is open.
  - `pending_nav`, `pending_create_project`, `pending_create_type`, `pending_project_browser` —
    a deferred-write pattern (see below).

**Why the `pending_*` pattern exists:** Streamlit raises `StreamlitAPIException` if code tries to
write `st.session_state[key]` for a key that already belongs to a widget instantiated earlier in
the *same* script run. The sidebar's `nav_page` radio is instantiated near the top of every run;
any code further down the same run (the command palette, an AI Agents "create task of this type"
button, a Workspace Launcher shortcut, Focus Mode's exit button) that wants to change the active
page cannot write `st.session_state.nav_page` directly. Instead, these call sites write a
`pending_nav` (and, where relevant, `pending_create_project` / `pending_create_type` /
`pending_project_browser`) key and call `st.rerun()`. At the very top of the *next* run — before
`st.set_page_config` and before any widget is created — a small loop applies each pending value to
its real widget key and removes the pending key. This is the only cross-page navigation mechanism
in the app; there is no separate router.

## 6. External process boundary

`subprocess.run` is called from exactly two categories of call sites, both with a fixed argument
list (never `shell=True`), `capture_output=True`, `text=True`, and an explicit timeout:

1. **`run_start_task_script`** → `scripts/start-task.sh <PROJECT> <TASK_TYPE> <OBJECTIVE>`
   (30s timeout). This is the only place the app can create a new AI task file. `stdout`/`stderr`
   are always captured and surfaced in the UI; a non-zero exit code is treated as failure and the
   Kanban record is not created.
2. **`run_git_command`** → `git <args...>` (5–10s timeout depending on call site), used only for
   read-only subcommands: `rev-parse`, `branch --show-current`, `branch --list`, `status
   --porcelain`, `log`, `diff --stat`, `remote -v`, `worktree list --porcelain`. No git subcommand
   that mutates repository or working-tree state (`commit`, `push`, `pull`, `merge`, `reset`,
   `rebase`, `checkout`, `add`, `stash`) is ever invoked.

Workspace Launcher intentionally does **not** spawn a file manager or editor (e.g. `open`,
`code`) — that would fall outside these two categories. Instead it navigates within the app and
prints copyable absolute paths for the user's own terminal/editor.

## 7. Data model

A Kanban task (one JSON object in the `data/tasks.json` array) has this shape:

```json
{
  "id": "uuid4 hex string",
  "project": "AIOS | BANK | LEGAL | BUSINESS | PERSONAL",
  "title": "the task objective, as entered",
  "task_type": "implementation | review | remediation | final_gate | architecture_review",
  "status": "Backlog | Next | In Progress | Review | Done",
  "priority": "Low | Medium | High | Critical",
  "owner": "free-text string, may be empty",
  "estimate_hours": 0.0,
  "depends_on": ["other task id", "..."],
  "created_at": "ISO 8601, second precision",
  "updated_at": "ISO 8601, second precision"
}
```

`depends_on` holds other tasks' `id`s. A task is **blocked** (`is_blocked`) if any dependency
either doesn't exist in the current task list or is not in `Done` status; `unmet_dependencies`
returns the list of such ids for display. Dependency resolution is done in memory each run
(`tasks_by_id = {t["id"]: t for t in tasks}`) — there is no foreign-key enforcement or cascade
delete, so deleting a task that others depend on leaves a dangling id, which the UI renders as
"(удалена) <id>" rather than failing.

## 8. Directory contract

The app treats these repository paths as its contract with the rest of the project; it never
writes outside `data/`:

| Path | Read | Written by app | Written by `scripts/start-task.sh` |
|---|---|---|---|
| `projects/<FILE>.md` | ✓ | — | — |
| `context/<FILE>_CONTEXT.md` | ✓ | — | — |
| `generated/<PROJECT>/*.md` | ✓ | — | ✓ |
| `reports/<PROJECT>/*.md` | ✓ | — | ✓ (report skeleton path) |
| `CURRENT_STATE.md`, `DECISIONS.md`, `INBOX.md` | ✓ | — | — |
| `data/tasks.json` | ✓ | ✓ | — |

## 9. Extension points

- **New page**: add one `NAV` entry and one `elif page_key == "...":` block. No other file needs
  to change.
- **New task field**: extend `new_task_record`, add a default in `normalize_task`, and render it
  wherever tasks are displayed (Kanban card, Focus Mode, Executive Dashboard).
- **New read-only git view**: add a `get_git_*` helper following the existing pattern (call
  `run_git_command`, parse `stdout`, return a plain Python structure) and render it in Git Center.
- **New cross-page shortcut**: stage a `pending_*` key and call `st.rerun()`, then add that key to
  `_PENDING_KEY_MAP` at the top of the Page setup section.

## 10. Explicitly out of scope

- No authentication, multi-user support, or network exposure beyond `localhost`.
- No database — `data/tasks.json` is the entire persistence layer.
- No mutating git operations from the UI, ever.
- No subprocess calls outside `scripts/start-task.sh` and read-only `git` subcommands (in
  particular, no spawning of external editors/file managers/browsers).
- No JavaScript/HTML component code — the UI is built entirely from native Streamlit elements
  (`st.button(shortcut=...)` provides the keyboard-shortcut behavior for the command palette
  without any custom component).
