# AI Command Center — Architecture

This document describes how the application is built as of **v1.2**. It reflects the code in
`app.py` and `command_center/`, not aspirations — if this file and the code disagree, the code is
authoritative. §§1–10 describe the v1.1 baseline (still accurate); §11 describes what v1.2 added.

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
        ├── subprocess ─────────────────────►│ git <read-only subcommand>
        │
        ├── calls ──────────────────────────►│ command_center/ (v1.2, see §11)
        │                                        ├── writes/reads data/runs.jsonl, chats.json,
        │                                        │   activity.jsonl, project_config.json
        │                                        ├── subprocess ──► claude CLI (-p, fixed args,
        │                                        │   timeout, restricted tool permissions)
        │                                        └── writes reports/<PROJECT>/*.md (full reports)
        └── subprocess (optional) ──────────►│ OpenAI SDK (Responses API), only if
                                                 OPENAI_API_KEY + OPENAI_MODEL are set
```

Everything the app knows comes from the filesystem under the repository root, plus
`data/tasks.json` and (v1.2) `data/runs.jsonl`, `data/chats.json`, `data/activity.jsonl`,
`data/project_config.json`. There is no hidden state anywhere else, and no database.

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

There is exactly one Python *script*: `app.py` is still the single Streamlit entry point and still
owns every page's rendering, session-state, and navigation logic — no `pages/`/`app_pages/`
directory, no `st.navigation`/`st.Page`. As of v1.2, `app.py` imports a small package,
`command_center/`, for everything that isn't Streamlit rendering: storage, project configuration,
the Claude Code runner, report parsing, chat providers, next-task suggestion, and the activity log.
See §11 for that package's module layout. The split follows one rule: if a function calls `st.*`,
it stays in `app.py`; if it doesn't, it lives in `command_center/`.

## 4. Pages

Each `NAV` entry is `key -> (label, material_icon)`; the sidebar `st.radio(key="nav_page")`
drives a flat dispatch:

| Key | Page | Purpose |
|---|---|---|
| `dashboard` | Обзор | Operational KPIs, active tasks by project, recent activity |
| `executive` | Исполнительная панель | Cross-project rollup, blocked tasks, priority/owner breakdowns, v1.2 run metrics |
| `create` | Создать задачу | Task-creation form → `scripts/start-task.sh` → Kanban record |
| `chat` | Чат по проекту | v1.2: per-project conversations, provider abstraction, save-to-report / to-task |
| `kanban` | Kanban | 5-column board with priority/owner/estimate badges, dependency blocking, and (v1.2) an agent launcher per task |
| `agents` | AI-агенты | Catalog of the 5 task types, their rules, usage stats, and (v1.2) a direct launcher |
| `runs` | Журнал запусков | v1.2: every Claude Code run, filterable, with parsed fields and Create Next Task |
| `timeline` | Таймлайн | Day-grouped feed of task events, file activity, runs, and activity-log events |
| `projects` | Проекты | Per-project status/generated/reports/context browser + (v1.2) repository-path settings |
| `generated` | Сгенерированные задачи | Global, recursive, project-filterable browser of `generated/`, with a launcher |
| `reports` | Отчёты | Global, recursive, project-filterable browser of `reports/`, with parsed run data when linked |
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
  - `pending_nav`, `pending_create_project`, `pending_create_type`, `pending_project_browser`,
    `pending_chat_conv` (v1.2) — a deferred-write pattern (see below).

**Why the `pending_*` pattern exists:** Streamlit raises `StreamlitAPIException` if code tries to
write `st.session_state[key]` for a key that already belongs to a widget instantiated earlier in
the *same* script run. The sidebar's `nav_page` radio is instantiated near the top of every run;
any code further down the same run (the command palette, an AI Agents "create task of this type"
button, a Workspace Launcher shortcut, Focus Mode's exit button, v1.2's "new conversation created"
handler) that wants to change the active page or another already-instantiated widget cannot write
`st.session_state.<key>` directly. Instead, these call sites write a `pending_nav` (and, where
relevant, `pending_create_project` / `pending_create_type` / `pending_project_browser` /
`pending_chat_conv`) key and call `st.rerun()`. At the very top of the *next* run — before
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
  "project": "AIOS | AICOS | BANK | LEGAL | BUSINESS | PERSONAL",
  "title": "the task objective, as entered",
  "task_type": "implementation | review | remediation | final_gate | architecture_review",
  "status": "Backlog | Next | In Progress | Review | Done",
  "priority": "Low | Medium | High | Critical",
  "owner": "free-text string, may be empty",
  "estimate_hours": 0.0,
  "depends_on": ["other task id", "..."],
  "created_at": "ISO 8601, second precision",
  "updated_at": "ISO 8601, second precision",

  "_comment": "v1.2 workflow fields — all optional, backfilled by normalize_task via command_center.models.normalize_task_workflow for records written before v1.2:",
  "parent_task_id": "prior task id this one was created from, or null",
  "prior_run_id": "run id this task was created from, or null",
  "current_run_id": "most recent run id launched for this task, or null",
  "workflow_stage": "Draft | Ready | Running | Remediation | Final Review | Approved | Commit Pending | Push Pending | PR Pending | Done",
  "latest_verdict": "last parsed verdict for this task, or null — parallel to, does not replace, `status`",
  "report_path": "repo-relative path to the latest full report, or null",
  "repository_path": "absolute path used for the latest run, or null",
  "branch": "branch observed after the latest run, or null",
  "agent": "e.g. \"claude_code\", or null",
  "last_run_at": "ISO 8601 timestamp of the latest run, or null"
}
```

`depends_on` holds other tasks' `id`s. A task is **blocked** (`is_blocked`) if any dependency
either doesn't exist in the current task list or is not in `Done` status; `unmet_dependencies`
returns the list of such ids for display. Dependency resolution is done in memory each run
(`tasks_by_id = {t["id"]: t for t in tasks}`) — there is no foreign-key enforcement or cascade
delete, so deleting a task that others depend on leaves a dangling id, which the UI renders as
"(удалена) <id>" rather than failing.

`workflow_stage` is a v1.2 addition that sits *alongside* the existing Kanban `status` — it is never
a replacement. `status` still drives the Kanban board's five columns; `workflow_stage` tracks
progress through the agent run → verdict → remediation/final-review → commit/push/PR pipeline (see
§11). A task can be `status: "In Progress"` and `workflow_stage: "Remediation"` at the same time.

See `command_center/models.py` for the run record, chat conversation/message, and activity event
shapes (`new_run_record`, `new_conversation`, `new_message`, `new_activity_event`) and
`command_center/report_parser.py`'s `empty_parsed_result()` for the parsed-report shape stored in a
run's `parsed` field.

Every timestamp in this app (v1.1's task `created_at`/`updated_at` and every v1.2 run/chat/activity
timestamp) is `datetime.now().isoformat(timespec="seconds")` — **naive local time, no timezone
offset**, deliberately kept identical to the pre-existing v1.1 convention rather than migrated to a
timezone-aware format, to avoid a mixed-format hazard against existing `data/tasks.json` records.
Read every timestamp in this app as "local time on the machine that wrote it."

## 8. Directory contract

The app treats these repository paths as its contract with the rest of the project; it never
writes outside `data/` and (v1.2) `reports/`:

| Path | Read | Written by app | Written by `scripts/start-task.sh` |
|---|---|---|---|
| `projects/<FILE>.md` | ✓ | — | — |
| `context/<FILE>_CONTEXT.md` | ✓ | — | — |
| `generated/<PROJECT>/*.md` | ✓ | — | ✓ |
| `reports/<PROJECT>/*.md` | ✓ | ✓ (v1.2, `agent_runner.save_report`) | ✓ (report skeleton path) |
| `CURRENT_STATE.md`, `DECISIONS.md`, `INBOX.md` | ✓ | — | — |
| `data/tasks.json` | ✓ | ✓ | — |
| `data/runs.jsonl`, `data/activity.jsonl` | ✓ | ✓ (append-only) | — |
| `data/chats.json`, `data/project_config.json` | ✓ | ✓ | — |

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
- No database — `data/*.json`/`*.jsonl` files are the entire persistence layer.
- No mutating git operations from the UI or from this app's own code, ever (the Claude Code
  subprocess it launches is itself blocked from git-write subcommands — see §11.3).
- No subprocess calls outside `scripts/start-task.sh`, read-only `git` subcommands, and (v1.2) the
  `claude` CLI (in particular, no spawning of external editors/file managers/browsers).
- No JavaScript/HTML component code — the UI is built entirely from native Streamlit elements
  (`st.button(shortcut=...)` provides the keyboard-shortcut behavior for the command palette
  without any custom component).
- No FastAPI, React, Docker, PostgreSQL, Redis, or Celery — v1.2 deliberately did not introduce any
  of these; the local single-process Streamlit model from §1 is unchanged.
- No Anthropic cloud SDK — the local Claude Code CLI is sufficient and is what v1.2 uses.

## 11. v1.2: Agent workflow architecture

### 11.1 `command_center/` module layout

| Module | Responsibility |
|---|---|
| `models.py` | Shared constants (`PROJECT_IDS`, `WORKFLOW_STAGES`, `RUN_STATUSES`, verdict constants, `SEVERITIES`) and plain-dict record factories (`new_run_record`, `new_conversation`, `new_message`, `new_activity_event`, `default_task_workflow_fields`) — the same "dict + `new_*`/`normalize_*` factory" convention `app.py` already used for tasks, not dataclasses/an ORM. |
| `storage.py` | Generic atomic-JSON and append-only-JSONL primitives (`atomic_write_json`, `read_json`, `append_jsonl`, `read_jsonl`, `fold_latest_by_id`, `ensure_seeded[_jsonl]`, `resolve_data_dir`). Every other module's persistence is built on these. |
| `project_config.py` | Project configuration: display name, sensitivity, allowed agents, context file paths, `reports_dir`/`generated_dir`, and the one locally-editable field, `repository_path` (stored in gitignored `data/project_config.json`). `discover_candidate_repository_path` only ever returns a path it has verified exists and is a git repo. |
| `agent_runner.py` | Everything about launching and recording a Claude Code run: repository-path validation, git snapshotting, `subprocess` execution, run persistence (`runs.jsonl`), and full-report file generation. The security-sensitive core — see §11.3. |
| `report_parser.py` | Deterministic, regex/heading-based extraction of verdict/findings/files/commit/branch/PR/validation/git-status/next-action from a report's text. Never invents a field; every unmatched field stays `None`/empty. Supports a manual-correction overlay that never discards the original extraction. |
| `chat_service.py` | Project Chat conversation storage (`chats.json`) plus the `ChatProvider` interface and its three implementations (`LocalProvider`, `ClaudeCodeChatProvider`, `OpenAIChatProvider`). |
| `workflow.py` | `suggest_next_task`: pure function, verdict → task-type/workflow-stage/objective-draft suggestion. Never creates or executes anything itself — `app.py`'s "Создать следующую задачу" button does that, after the user reviews the draft. |
| `activity_log.py` | Append-only event log (`activity.jsonl`). |

`app.py` is the only module that calls `st.*`; every `command_center` module is plain Python,
independently unit-testable, and imports nothing from `app.py` (the dependency direction is always
`app.py → command_center`, never the reverse).

### 11.2 Runtime storage: JSON vs. JSON Lines

`data/tasks.json` and `data/chats.json` stay whole-file JSON (read-modify-write documents, matching
the v1.1 `tasks.json` pattern: atomic temp-file + `os.replace` on every write). `data/runs.jsonl`
and `data/activity.jsonl` are **JSON Lines** instead: both are write-heavy logs where a run record
can carry a large `stdout` blob and gets appended to at queued/running/completed. Rewriting an
entire multi-run JSON array on every status transition would be slower and would put the *whole*
history at risk during that rewrite; JSONL reduces that window to a single `open(..., "a")` + one
line + `fsync`. "Current state" of a run is the last line seen for its id — `storage.fold_latest_by_id`
performs that fold on load. See `command_center/storage.py`'s module docstring for the same
reasoning in code.

Every `data/*.json(l)` runtime file is **gitignored** and starts genuinely empty on a fresh
checkout — `storage.ensure_seeded`/`ensure_seeded_jsonl` deliberately never read the tracked
`data/*.example.*` sibling file to seed the real one; those `.example` files hold illustrative
sample content for documentation only. (An earlier draft of this feature did seed from the example
files — caught during manual testing before release, because it silently presented a fabricated
AIOS repository path and a fake chat conversation as if they were real local data.)

`AICC_DATA_DIR` (env var, read by `storage.resolve_data_dir`) redirects every module's data
directory at once. It exists for the test suite, which sets it once in `tests/conftest.py` before
any module is imported, so tests never touch a developer's real `data/`.

### 11.3 Security boundaries (Claude Code runner)

- `agent_runner.run_claude_code` calls `subprocess.run` exactly like `run_start_task_script`/
  `run_git_command` already did: a fixed argument list, `shell=True` never used, `capture_output=True`,
  `text=True`, an explicit timeout. The prompt is one argv element passed straight to the `claude`
  binary — never interpreted by a shell — so prompt content cannot inject shell commands.
- `agent_runner.validate_repository` refuses to run unless the requested `repository_path` resolves
  (symlinks/`..` included) to *exactly* the path configured for that project in
  `project_config.load_project_configs()`. A task can never be launched against a repository path
  that isn't in the project configuration, and an unconfigured project refuses outright.
- **This application's own code** — the Python running under Streamlit — never calls
  `git commit`/`push`/`merge`/`reset`/`rebase`/`clean`/`add`/`apply`/`checkout`/`restore`/`switch`/
  `stash`, automatically or otherwise. The only git subprocess calls `agent_runner` itself makes are
  the read-only pre/post-run snapshot (`rev-parse`, `branch --show-current`, `status --porcelain`).
  This guarantee is unconditional — it does not depend on what a spawned `claude` process does, and
  it is the only prohibition in this section that is absolute for every task type.
- **Read-only task types** (`review`, `final_gate`, `architecture_review`) get genuine technical
  enforcement that they cannot modify the repository: `agent_runner.build_command` passes `--tools
  Read,Grep,Glob` (`READ_ONLY_ALLOWED_TOOLS`). Per `claude --help`, `--tools` replaces the *entire*
  available tool set for that run rather than layering a permission rule on top of it — `Bash`,
  `Edit`, `Write`, `NotebookEdit`, and `MultiEdit` are simply not in the list the model is given, so
  none of them can be invoked, by any means, for that run. This is what actually justifies "cannot
  modify any file" for these three task types: not a prompt instruction, and not a Bash pattern
  denylist (an earlier version of this module tried exactly that — denying specific
  `Bash(git ...)` patterns while leaving the general-purpose `Bash` tool itself available — and an
  independent review correctly found that left `git apply`/`checkout`/`stash`, plain shell
  redirection, and everything else reachable through Bash completely unrestricted; that approach was
  replaced with the `--tools` allowlist described here).
- **Implementation/remediation task types** keep the `Bash` tool — they need it to run tests,
  linters, and other validation per the `AGENT_ROLES` prompt rules in `scripts/start-task.sh` — but
  `--disallowedTools` is set to `GIT_WRITE_DISALLOWED_TOOLS`, a pattern-based denylist covering every
  git-write operation those task types' own prompts already forbid (`add`, `apply`, `checkout`,
  `restore`, `switch`, `stash`, `commit`, `push`, `merge`, `reset`, `rebase`, `clean`, branch
  deletion). Unlike the read-only case, this is **not** a tool-removal guarantee: these task types
  are expected to edit files (that's the job), and a denylist cannot enumerate every way a shell
  could mutate a repository outside of git — the boundary actually enforced here is specifically "no
  git-write operations," not "no file changes" and not "no shell access."
- The UI always shows the exact repository, branch, agent, and prompt, and requires an explicit
  confirmation checkbox before `run_claude_code` is ever called (`app.py`'s `render_agent_launcher`);
  this confirmation is re-required on every launch (there is no "remember this" state), and nothing
  in the app triggers a launch from a page load or an unrelated rerun.
- BANK/LEGAL are `sensitive` in `project_config`; the launcher and Project Chat show an explicit
  warning for them and never auto-attach context files — context is always pasted in by hand.
- No automated test invokes the real `claude` CLI or spends API credits: `tests/test_agent_runner.py`
  and `tests/test_app_streamlit.py` monkeypatch `subprocess.run` (delegating non-`claude` calls,
  i.e. the git snapshot, to the real `subprocess.run` against a throwaway `tmp_path` repo). The one
  real invocation of `claude` in this project's history was a manual, disposable, few-cents smoke
  test in a scratch git repo during development — never part of the automated suite.

### 11.4 Recovering from a failed/stuck run

Because the runner is synchronous (§ "v1.2 — Agent Workflow" in README.md), a run can only be
"stuck" for as long as its configured timeout — after that, `subprocess.run(..., timeout=...)`
raises `TimeoutExpired` and the run is recorded as `timed_out` with whatever partial stdout/stderr
were captured. If the Streamlit page itself seems frozen while a run is in flight: it is — the
script run is blocked on the subprocess by design (see §11.3). Reloading the browser tab does not
stop the underlying `claude` process (Streamlit's rerun model has no handle to cancel a subprocess
started by a *previous* run); if you need to stop it, find and terminate the `claude` process
directly (e.g. `pkill -f "claude -p"`) from a terminal. The run record will then be missing its
terminal state — this app does not fabricate one — so re-launch a fresh run for that task afterward
rather than trusting a run that never reached `completed`/`failed`/`timed_out` in `data/runs.jsonl`.
