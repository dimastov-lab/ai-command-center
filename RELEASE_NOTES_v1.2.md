# AI Command Center v1.2 — Release Notes

**Date:** 2026-07-16
**Scope:** Agent Workflow — Project Chat, Claude Code runner, structured report extraction,
Create Next Task, run journal.

## Independent review remediation

An independent security review of this branch found two issues before commit, both resolved:

- **F-01 (Blocker)** — the initial read-only task-type restriction (`review`/`final_gate`/
  `architecture_review`) denied specific `Bash(git ...)` patterns while leaving the general-purpose
  `Bash` tool itself available, so `git apply`/`checkout`/`stash` and plain shell file writes were
  still reachable. Fixed by switching those task types to `--tools Read,Grep,Glob` — a tool-set
  allowlist that removes `Bash` (and every file-edit tool) from what the run can invoke at all,
  rather than trying to enumerate everything Bash must not do. Implementation/remediation task
  types keep `Bash` but now have a broader, explicit git-write denylist (`add`/`apply`/`checkout`/
  `restore`/`switch`/`stash`/`commit`/`push`/`merge`/`reset`/`rebase`/`clean`/branch-delete).
- **F-02 (High)** — documentation (`ARCHITECTURE.md`, `command_center/agent_runner.py`) claimed a
  stronger guarantee than the code provided. Rewritten to state precisely which task types get
  which enforcement, and that implementation/remediation's guarantee is "no git writes," not "no
  repository changes."

See `CHANGELOG.md` under "Security" for the equivalent, shorter summary.

## Highlights

- **Project Chat** — a new page: per-project conversations with three interchangeable providers
  (local/manual, Claude Code CLI, optional OpenAI Responses API), full history with timestamps and
  roles, save-any-message-to-report, and convert-any-message-to-task.
- **Claude Code runner** — launch Claude Code from a Kanban task's detail panel, the Agents page,
  Project Chat, or a generated-task preview. Every launch shows the exact repository, branch,
  agent, and prompt and requires explicit confirmation; execution is synchronous, timeout-bounded,
  and fully captured (stdout, stderr, duration, exit code).
- **Full report storage** — every completed run's report is saved, untruncated, under
  `reports/<PROJECT>/`.
- **Structured result extraction** — a deterministic parser pulls verdict, findings by severity,
  files touched, commit hash, branch, PR URL, validation result, git status, and a recommended
  next action out of a report, with evidence snippets, a confidence level, and a manual-correction
  UI that never discards the original extraction.
- **Create Next Task** — on a completed run, a verdict-driven draft (task type, workflow stage,
  objective) that always requires review before creating anything and never auto-executes.
- **Run journal** — a new "Журнал запусков" page: every run, filterable by project/agent/status/
  verdict/date/task, with a full detail view (prompt, report, stderr, parsed fields, linked/next
  task, repository snapshot before/after). Executive Dashboard gained run metrics.
- **Repository configuration** — Projects → "Настройки репозитория": the only place a repository
  path is set, stored locally, never guessed (a suggestion is only ever a verified-existing git
  repo you must save yourself).
- **Sensitive-project handling** — BANK/LEGAL show an explicit warning before any agent launch or
  chat call and never auto-attach context files.
- **AICOS** added to the project registry (repository path intentionally left unconfigured — no
  confidently known local path for it on this machine).

## Compatibility

- Existing `data/tasks.json` records load without modification: the ten new v1.2 workflow fields
  (`parent_task_id`, `prior_run_id`, `current_run_id`, `workflow_stage`, `latest_verdict`,
  `report_path`, `repository_path`, `branch`, `agent`, `last_run_at`) are backfilled with their
  documented defaults (mostly `null`, `workflow_stage: "Draft"`) the first time each task loads.
- `workflow_stage` sits alongside the existing Kanban `status` — it does not replace it, and no
  v1.1 page's rendering of `status` changed.
- All v1.1 pages, subprocess boundaries, and Git read-only behavior are unchanged. `scripts/
  start-task.sh` and its AIOS/BANK/LEGAL-only template pipeline are untouched.

## Architecture

`app.py` remains the single Streamlit entry point. New logic that doesn't call `st.*` was
extracted into a `command_center/` package (`models`, `storage`, `project_config`, `agent_runner`,
`report_parser`, `chat_service`, `workflow`, `activity_log`) — see `ARCHITECTURE.md` §11 for the
full module layout, the JSON-vs-JSON-Lines storage decision, and the security boundaries around
the Claude Code subprocess. No FastAPI, React, Docker, PostgreSQL, Redis, Celery, or Anthropic
cloud SDK was introduced.

## Security controls

- `subprocess.run` is always called with a fixed argument list (never `shell=True`), an explicit
  timeout, and captured stdout/stderr — for `scripts/start-task.sh`, read-only `git`, and (new)
  the `claude` CLI alike.
- A run is refused unless its repository path resolves to *exactly* the path configured for that
  project — never an arbitrary path, and never an unconfigured project.
- This app's own code never calls a git-write subcommand — that guarantee is absolute for every
  task type. For the model's own actions, the two task-type classes are enforced differently:
  read-only task types (`review`/`final_gate`/`architecture_review`) run with `--tools
  Read,Grep,Glob` — `Bash` and every file-edit tool are entirely absent from the tool set, not
  merely pattern-denied, so nothing reachable through them (including `git apply`/`checkout`/
  `stash` or plain shell writes) is possible. Implementation/remediation task types keep `Bash`
  (they need it for tests/linters) but have the specific git-write subcommands
  (`add`/`apply`/`checkout`/`restore`/`switch`/`stash`/`commit`/`push`/`merge`/`reset`/`rebase`/
  `clean`/branch-delete) denied via `--disallowedTools` — a narrower guarantee ("no git writes,"
  not "no repository changes") appropriate to task types whose job is to edit files.
- Every launch requires an explicit confirmation checkbox after seeing the exact repository,
  branch, agent, and prompt.
- BANK/LEGAL show a sensitivity warning and never auto-attach files, on every launch surface.
- Create Next Task never executes the task it creates, and never performs a git write for
  commit/push/PR stages — those stay user-controlled workflow stages.

## Known limitations

- The runner is **synchronous**: Streamlit's re-run-the-whole-script model has no supervisor that
  could safely interrupt a subprocess already in flight from a previous run, so there is no
  mid-flight cancel button. A run can only end as `completed`/`failed`/`timed_out` (the timeout is
  user-configurable per launch). See `ARCHITECTURE.md` §11.4 for recovering from a run that seems
  stuck.
- The OpenAI provider requires the optional `openai` package, which is intentionally **not** in
  `requirements.txt`; install it yourself if you want to use that provider. Without it (or without
  `OPENAI_API_KEY`/`OPENAI_MODEL` set), the provider reports itself unavailable and the rest of the
  app — including the other two chat providers — is unaffected.
- The report parser is deterministic (regex/heading-based), not an LLM call: unusually formatted
  reports may leave some fields unmatched (shown as "not provided," never invented). The Runs page
  has a manual-correction UI for exactly this case.
- Only `AIOS` has a repository path this app can verify on this machine; `AICOS`, `BANK`, `LEGAL`,
  `BUSINESS`, and `PERSONAL` show "Repository path not configured" until set manually.
- `reports/` is now gitignored (it may hold BANK/LEGAL content); only the pre-existing top-level
  `reports/.gitkeep` remains tracked.

## Validation performed

- `python -m py_compile` on every Python file in `app.py`, `command_center/`, and `tests/`.
- `ruff check app.py command_center/ tests/` — clean.
- `pytest` (139 tests: atomic storage, task migration, path validation, report parser — including
  every required verdict phrase, contradictory-verdict resolution, and commit-hash extraction —
  next-task mapping, full report persistence, run-journal filtering, sensitive-project warnings,
  refusal to run against an unconfigured path, refusal to let a prompt reach a shell, and (post
  independent review) that read-only task types never receive an unrestricted `Bash` tool) — all
  passing.
- Streamlit `AppTest` coverage: Dashboard, Executive Dashboard run metrics, Project Chat render
  (including the BANK sensitivity warning), Runs page render, the Kanban task → Claude runner
  confirmation flow (including a full mocked launch → parsed verdict → run record), a completed
  run's Create Next Task button creating a real Backlog task, and run-journal project filtering.
- A live Streamlit server smoke test (`/_stcore/health` → HTTP 200).
- `git diff --check`, `git status`, and a manual review of the final diff for unrelated changes.

## Upgrade steps

```bash
pip install -r requirements.txt          # unchanged in this release
pip install -r requirements-dev.txt      # only if you want to run the test suite
```

No data migration step is required — existing `data/tasks.json` records are backfilled
automatically on next load. If you plan to use the Claude Code runner or the Claude Code chat
provider, install/authenticate the `claude` CLI and set a project's repository path from
Projects → "Настройки репозитория" first.
