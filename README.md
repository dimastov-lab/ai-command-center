# AI Command Center

Local control center for managing the AIOS, AICOS, BANK, LEGAL, BUSINESS and PERSONAL projects:
task creation, a Kanban board, generated AI task files, reports, repository/context status, and
(as of v1.2) project chat plus a Claude Code agent-run workflow with a parsed-result run journal —
all from one Streamlit application running on your machine.

A native desktop application is planned as the future daily-use interface; the Streamlit
application documented below remains the primary interface until then — see
[`docs/desktop/README.md`](docs/desktop/README.md) for the desktop architecture and design
documentation (status: D0, documentation only, no desktop code yet).

## Getting started

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
# for running the test suite too:
pip install -r requirements-dev.txt
```

### 3. (Optional) Configure environment variables

`.env.example` documents the optional variables (see "v1.2 — Agent Workflow" below).
The app does **not** auto-load a `.env` file (no `python-dotenv` dependency, to keep
requirements minimal) — export what you need in your shell before starting the app, or
use a tool like `direnv`. Every variable is optional; the app starts and works fully
with none of them set.

### 4. Start the application

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

## Runtime Data

- `data/tasks.json` is local runtime state — the live Kanban task store. It is **gitignored** and
  never committed; its contents are specific to your machine. Writes are atomic (temp file +
  `os.replace`), and older task records missing newer fields are backfilled with defaults when
  loaded, so existing data keeps working across app updates.
- `data/tasks.example.json` is the **version-controlled** seed template (`[]`, an empty task
  list). On startup, if `data/tasks.json` does not exist yet, the app copies
  `tasks.example.json` to `tasks.json` before loading tasks — so a fresh checkout always starts
  with a valid, empty store instead of failing or crashing.
- `generated/` holds transient AI task files produced by `scripts/start-task.sh`. It is
  **gitignored**; only the directory scaffolding (`.gitkeep`) is tracked.
- **v1.2 runtime files** — all **gitignored**, all start empty on a fresh checkout (never seeded
  from their tracked `.example` sibling; see "Runtime storage" below for why):
  - `data/runs.jsonl` — append-only Claude Code run log (JSON Lines).
  - `data/chats.json` — Project Chat conversations.
  - `data/activity.jsonl` — append-only activity/event log (JSON Lines).
  - `data/project_config.json` — local repository-path overrides, edited from the app's
    Projects → "Настройки репозитория" tab, never via hand-editing.
  - `reports/<PROJECT>/` — full Markdown reports from completed agent runs. As of v1.2 this whole
    directory is gitignored (it may contain BANK/LEGAL content); only the top-level `.gitkeep` that
    predates this change remains tracked.
  - Each of the four data files above has a tracked `data/*.example.*` sibling — documentation of
    the shape only, with illustrative sample content. It is **never** read at app startup.

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

## v1.2 — Agent Workflow

The first useful agent-integrated workflow: chat with a project, launch Claude Code
against a task, capture and parse its report, spin up the next task from the result, and
see every run in one journal.

### Prerequisites

- **Claude Code CLI** (`claude`) installed and authenticated on your machine — this is
  what the runner and the "Claude Code" chat provider shell out to
  (`claude -p <prompt> --output-format json --permission-mode acceptEdits
  --disallowedTools ...`). If it isn't on `PATH`, the app still starts; those two
  features report themselves unavailable instead of failing.
- Nothing else is required. The OpenAI provider (below) is fully optional.

### Project Chat

A new "Чат по проекту" page: pick a project, create or resume a conversation, and talk
to one of three providers:

- **Локальный / ручной режим (local)** — always available; you write both sides of the
  conversation yourself (e.g. paste in a response from elsewhere). This is the only
  provider the app depends on.
- **Claude Code (локальный CLI)** — shells out to the local `claude` CLI using the same read-only
  tool set as the `review` task type (`--tools Read,Grep,Glob`): `Bash` and every file-edit tool are
  entirely absent from that run, not merely pattern-denied — a chat turn cannot modify the
  repository.
- **OpenAI (Responses API)** — only appears available when both `OPENAI_API_KEY` and
  `OPENAI_MODEL` are set in the environment and the `openai` package is installed. The
  model is **never** hard-coded — it always comes from `OPENAI_MODEL`. The API key is
  read directly from the environment by the OpenAI SDK; this app never stores, logs, or
  echoes it. **OpenAI API usage is billed separately from a ChatGPT subscription.**

Any message can be saved into `reports/<PROJECT>/` or converted into a Kanban task.

### Claude Code Runner

Launch Claude Code from a Kanban task's "Детали" panel, the Agents page, Project Chat,
or a generated-task preview. Every launch:

1. Shows the exact repository, branch, agent, and prompt, and requires an explicit
   confirmation checkbox before anything runs.
2. Refuses to run unless the repository path is *exactly* the one configured for that
   project (Projects → "Настройки репозитория") — never an arbitrary path.
3. Records a pre-run git snapshot (branch/HEAD/status), executes synchronously with a
   timeout, and captures the complete stdout/stderr, duration, and exit code.
4. Saves the full, untruncated report under `reports/<PROJECT>/`.
5. Parses the report deterministically (verdict, findings by severity, files
   touched, commit hash, branch, PR URL, recommended next action — see below) and
   updates the linked task's workflow fields.

**What "read-only" actually means, precisely**: `review`/`final_gate`/`architecture_review` runs
get a restricted *tool set* (`--tools Read,Grep,Glob`) — `Bash` and every file-edit tool are absent
from the run entirely, not merely denied by pattern, so the model has no path to modify the
repository. `implementation`/`remediation` runs keep `Bash` (they're expected to edit files and run
tests/linters) but have the specific git-write subcommands denied. Neither this app nor the model it
launches ever performs a commit, push, or merge automatically, for any task type — that prohibition
is enforced by this application's own code simply never calling those commands, not by anything the
model is or isn't allowed to do. See ARCHITECTURE.md §11.3 for the full breakdown.

**Execution model / known limitation**: Streamlit re-runs the whole script on every
interaction, so there is no supervisor process that could safely interrupt a subprocess
already in flight from a previous run. The runner is therefore **synchronous** — the
page blocks (with a spinner) until the agent finishes or the timeout fires. There is no
mid-flight cancel button (a `cancelled` status exists but is only reached if the process
never started). This was a deliberate choice: a robust synchronous runner over a
simulated "live" status that isn't real.

### Structured result extraction

`command_center/report_parser.py` deterministically extracts, when present: verdict
(`APPROVED FOR COMMIT` / `NOT APPROVED FOR COMMIT` / `READY FOR FINAL REVIEW` /
`NOT READY FOR FINAL REVIEW` / `READY FOR COMMIT` / `FAILED`), findings by severity
(Blocker/High/Medium/Low), files modified/created/deleted, commit hash (full or short),
commit message, branch, remote, PR URL, validation result, exact git status, and a
recommended next action. Nothing is invented — unmatched fields stay `None`/empty and
render as "not provided." Every field carries evidence and a confidence level
(none/low/medium/high), and the Runs page has a manual-correction UI that overlays a
correction without discarding the original extraction.

### Create Next Task

On a completed run, "Создать следующую задачу" prefills a task type and objective from
the verdict/findings/recommended action (e.g. `NOT APPROVED FOR COMMIT` → remediation,
`READY FOR FINAL REVIEW` → final_gate), but always requires review before creating
anything, and never executes the new task automatically. Commit/push/PR are treated as
user-controlled workflow stages (`workflow_stage`), never as automatic git writes.

### Run journal

The new "Журнал запусков" page lists every run with filters (project, agent, status,
verdict, date range, task) and a detail view (full prompt/report/stderr, parsed fields,
linked task, next task, repository snapshot before/after). The Executive Dashboard
gained run metrics (runs today, success/failure counts, awaiting remediation/final
review, approved-for-commit, average duration by agent, open Blocker/High findings).

### Sensitive projects (BANK, LEGAL)

Both are flagged `sensitive` in project configuration. Anywhere an agent can be
launched or chatted with for these projects, the UI shows an explicit warning and never
auto-attaches context files — you select or paste in whatever context is permitted,
every time.

### Repository configuration

Projects → "Настройки репозитория" is the only place a repository path is set; it's
stored locally in the gitignored `data/project_config.json`. No path is ever guessed —
`AIOS` is the only project whose path this app can verify on a typical checkout (it
checks a candidate exists **and** is a git repository before ever suggesting it, and
even then only as a pre-filled suggestion you must save). Every other project shows
"Repository path not configured" until you set one.

## Scope and limitations

- Local-only tool: no authentication, no network services, no database.
- Read-only Git everywhere (status, log, diff, branches, remotes, worktrees): the UI never runs
  commit, push, pull, merge, reset, rebase, or checkout.
- All subprocess calls use fixed argument lists (never `shell=True`), explicit timeouts, and
  captured stdout/stderr — limited to `scripts/start-task.sh`, read-only `git` subcommands, and (as
  of v1.2) the `claude` CLI, which itself is never permitted to run mutating git subcommands (see
  ARCHITECTURE.md "Security boundaries").
- Workspace Launcher does not spawn external editors/file managers (out of scope for the allowed
  subprocess surface); it navigates within the app and shows copyable local paths instead.
- The Claude Code runner is synchronous (see "v1.2 — Agent Workflow" above) — no real mid-flight
  cancellation. If a run appears stuck, see "Recovering from a stuck run" in ARCHITECTURE.md.
- The OpenAI provider requires the optional `openai` package, which is **not** in
  `requirements.txt` (kept minimal); install it yourself if you want that provider.
- `scripts/start-task.sh` and its file-template pipeline are unchanged and still only recognize
  `AIOS`, `BANK`, `LEGAL`; the v1.2 Claude Code runner and Create Next Task, however, work for all
  six projects once a repository path is configured (BUSINESS/PERSONAL do not have one yet on a
  typical checkout).
- Focus Mode's sidebar auto-collapse relies on Streamlit's `initial_sidebar_state`, which is a
  best-effort, browser-session-scoped hint rather than a guaranteed collapse on every rerun.
- `scripts/start-task.sh` currently only recognizes the `AIOS`, `BANK`, and `LEGAL` projects;
  creating a task for `BUSINESS` or `PERSONAL` will surface the script's error in the UI.
- Only `AIOS`, `BANK`, and `LEGAL` have dedicated context files under `context/`; `BUSINESS` and
  `PERSONAL` are handled gracefully with an informational message.
- No drag-and-drop on the Kanban board; moving a task between columns is done via a dropdown.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The suite (`tests/`) isolates itself from your real local data via the `AICC_DATA_DIR`
environment variable (set once in `tests/conftest.py`, before any app/`command_center`
module is imported) — it never reads or writes your real `data/*.json(l)` files. It
mocks `subprocess.run` for every scenario that would otherwise invoke the real `claude`
CLI or spend API credits; no automated test launches a real agent job.
