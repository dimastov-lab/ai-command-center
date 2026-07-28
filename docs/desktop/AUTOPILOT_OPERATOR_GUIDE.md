# Desktop Autopilot — operator guide

The autopilot turns the Execution Queue into a working pipeline: it ranks and
plans the next parallel-safe wave of tasks, optionally launches them, drives
each finished run through validation → pull request → merge, projects the
verified merge back onto the Kanban board, and immediately recomputes what can
run next.

It is **off by default** and every switch is an explicit, persisted opt-in.

---

## 1. What it actually does

One *tick* — a single bounded pass, run from the Live Execution Center's
existing refresh cycle — performs, in this order:

1. **reconcile** — repair every persisted `RUNNING` row whose OS process died;
2. **reconcile merge policy** — bring completion rows in line with the current
   auto-merge opt-in *before* anything advances on a stale policy;
3. **advance completions** — move rows already mid-flight (validating, PR open,
   merged-not-yet-verified);
4. **sync** — project run state onto tasks, seed a completion row for every
   newly finished successful run;
5. **advance again** — so a row seeded in step 4 progresses on this same tick;
6. **sync again** — project the states reached in step 5;
7. **close verified merges** — a task whose completion reached `COMPLETED`
   (merge *verified present in the target branch*) moves to Kanban `Done`;
8. **plan** — refresh queue readiness against those newly-`Done` tasks and ask
   the scheduler for the capacity- and workspace-safe wave;
9. **dispatch** — launch only the `ASSIGN` decisions, only if auto-launch is on;
10. **re-plan** — show the wave that follows this dispatch.

Because step 7 runs before step 8, a task unblocked by a merge is planned **and
launched on the same tick** — you do not wait a cycle for the next wave.

**Steps 3 and 5 do not run on the render thread.** A completion advance can take
minutes — it shells out to the project's validation commands (900-second
timeout) and performs `git fetch` and `gh` round trips — so it is executed by a
short-lived worker thread and the tick waits at most ~1 second for it. A fast
advance is reported by the tick that started it; a slow one keeps running and is
reported by a later tick, with nothing lost. This is what keeps the dashboard
responsive: the screen you use to *cancel* a runaway run must never be frozen by
the pipeline.

There is no polling loop and no long-lived background thread: a worker exists
only for the duration of one advance, at most one per database at a time. If the
tick is not run, the autopilot does nothing.

---

## 2. Turning it on

Live Execution Center → **Автопилот рабочего стола**.

| Control | What it permits | Default |
|---|---|---|
| Включить автопилот | Master switch. While off, no other switch does anything. | off |
| Автозапуск готовых задач | Start the tasks the planner marks `ASSIGN`. | off |
| Автоматический merge после проверок | Raise completion policy from `manual` to `auto_after_checks`. | off |
| Независимая проверка перед PR | Blocking gate: a separate read-only reviewer must approve before any pull request is opened. | off |
| Автодоработка при провале проверки | Relaunch the same task as a new attempt, carrying the failure into its prompt. | off |
| Попыток доработки | Rework budget per task (0–5). | 2 |
| Автоочистка своих worktree | Stash leftovers in a worktree the pipeline owns. Never touches your primary tree. | off |
| Всего параллельно | Global concurrent-run cap (1–16). | 2 |
| На агента | Per-agent concurrent-run cap (1–16). | 2 |

Settings are stored in `data/pipeline_settings.json` and survive reruns, page
switches and restarts — the file answers "what is this machine permitted to do",
which is deliberately not a per-browser-tab question.

Parsing is fail-closed: a missing, truncated or hand-edited settings file, or a
value of the wrong type, resolves to the **off** default. Only a literal JSON
`true` enables a switch, so a stray `"false"` string can never turn one on.

**With auto-launch off** the tick still reconciles, advances completions and
plans the wave — you see exactly what *would* start, and start it yourself from
the queue. This is the recommended way to run it for the first few days.

---

## 3. Reading the wave

Each row shows the task, its action, priority, assigned agent, workspace,
attempt number, and the planner's own rationale.

| Action | Meaning |
|---|---|
| 🟢 Назначено | Scheduled to run now, as an explicit new attempt. |
| 🟡 Отложено | Eligible, but a *transient* condition blocks it — re-planning later will clear it. |
| 🔴 Заблокировано | Cannot run as-is; re-planning alone will not help. A human or config change is required. |
| ⚪️ Пропущено | Never reached the planner (its task is gone, its workspace is unusable, or it needs manual confirmation). |

Anything that did not start shows a machine-readable code and what to do about
it. The common ones:

| Code | What it means | What to do |
|---|---|---|
| `waiting_dependency` | A dependency is not `Done` yet. | Finish the dependency — `Done` requires a verified merge. |
| `workspace_busy` | Another active run holds this workspace. | Wait, or cancel that run. |
| `global_at_capacity` / `agent_at_capacity` | Concurrency cap reached. | Raise the cap, or wait. |
| `duplicate_task_assignment` | The task already has an active attempt. | Wait for it to finish. |
| `backoff` | A recoverable failure; the retry timer has not elapsed. | Wait until `Следующая попытка не раньше`. |
| `retry_exhausted` / `terminal_failure` | Retrying cannot help. | Fix the cause, launch a new attempt manually. |
| `needs_confirmation` | Dirty tree, detached HEAD or branch mismatch. | Launch manually from the task card and acknowledge the warnings. |
| `workspace_verification_failed` | The workspace is not an isolated worktree of the project repository, or is on the wrong branch. | Check `workspace_path`, `branch`, and the project's `repository_path`. |
| `launch_blocked` | The workspace is missing or is not a git repository. | Fix the path. |
| `workspace_not_configured` | No workspace resolved for the task. | Set the task's `workspace_path` or the project's `repository_path`. |

---

## 4. What the autopilot never bypasses

These hold regardless of any setting:

- **Sensitive-content confirmation.** A BANK/LEGAL project's outbound content
  still goes through `context_service`'s confirmation boundary.
- **Workspace isolation.** A feature-branch task must run in an isolated linked
  worktree, never in the primary working tree. The check is fail-closed at
  `Supervisor.start_raw` — the last gate before a process is spawned.
- **Warning acknowledgement.** A task whose workspace has warnings (dirty tree,
  detached HEAD, branch mismatch) is never auto-launched. There is no per-task
  human in the loop in a batch, so it is left for a manual, confirmed launch.
- **Merge gates.** The auto-merge opt-in only changes *policy*. Whether a given
  pull request may merge is still decided entirely by checks passing, required
  review, mergeability and conflict state.
- **`Done` means merged and verified.** A task reaches `Done` only after the
  merge is confirmed reachable from the target branch — not when a PR is opened,
  and not when GitHub reports a merge.
- **One attempt per task and per workspace.** Enforced independently by the
  host-wide pipeline lock, by the planner, and by the launcher's own duplicate
  check.

---

## 4a. The independent review gate

With **Независимая проверка перед PR** on, a change that has passed validation
does *not* go straight to a pull request. The completion parks in
`AWAITING_REVIEW`, and a separate reviewer run is started against the same
worktree with task type `review`.

That reviewer is read-only **by process contract, not by instruction**: the
read-only execution profile hands the process a tool set with no `Bash`, no
`Edit`, no `Write` and no `NotebookEdit`, so it physically cannot modify the
change it is judging. It reads the diff, reports problems, and ends with an
explicit verdict.

- **Approved** → the pull-request phase proceeds normally.
- **Rejected** → the completion becomes `REVIEW_REJECTED` (terminal), the task
  shows `Requires Attention`, and — if rework is enabled — the task is
  relaunched with the reviewer's reasoning in its prompt.
- **No verdict, unreadable output, or a reviewer that crashed** → treated as
  **rejection**. A blocking gate that opened a pull request on an unreadable
  review would not be a gate.

The gate cannot be bypassed by re-running the pipeline: `AWAITING_REVIEW` has no
transition back to the pre-gate state, and `REVIEW_REJECTED` has no outgoing
transitions at all. Both properties are asserted directly in
`tests/test_independent_review.py`.

Cost note: this is one extra agent run per task. That is the trade being made
for "nothing reaches a pull request unreviewed".

---

## 5. Auto-merge, precisely

With the opt-in **on**, a completion row whose configured merge mode is `manual`
is raised to `auto_after_checks`, and the change is recorded as a
`MERGE_POLICY_APPLIED` audit event on that row.

- A task or project that explicitly configures
  `auto_after_checks_and_review` keeps it — the opt-in only ever raises
  `manual`, never lowers or overrides a stronger explicit choice.
- Turning the opt-in **off** writes the configured mode back on the next tick,
  so a row cannot keep auto-merging on the strength of a setting you have since
  disabled.
- Rows that have already merged (`MERGED`, `VERIFYING_TARGET_BRANCH`) are never
  re-policied — there is nothing left to decide.

---

## 6. Concurrency and safety

Two desktop sessions (or a session plus a scheduled tick) cannot both plan
against the same free capacity: the tick holds a host-wide advisory lock
(`data/task_pipeline.lock`) for its whole pass. A tick that cannot take the lock
reports `Тик пропущен — конвейер занят другим процессом` and returns
immediately, because the holder is already doing the identical work.

A single failed item never aborts the wave. A launch refused by the isolation
gate, a transient GitHub or git fault during a completion advance, or a failure
in the Kanban projection are each isolated and reported; everything else in the
same tick still proceeds.

The tick holds your thread for at most the pipeline-lock timeout plus the
advance wait (~1s per advance point). Slow work is always deferred to the
worker, never inlined.

---

## 7. Audit trail

- **Per decision** — every entry in the wave carries `action`, `reason_code`,
  the planner's explanation and, if it did not start, `launch_reason_code` plus
  the launch message. `PipelineTickResult.as_dict()` is fully JSON-serializable.
- **Activity log** (`data/activity.jsonl`) — `pipeline_launched`,
  `pipeline_skipped`, `pipeline_task_completed`.
- **Completion events** (per run, in `runtime.db`) — the full
  validation → PR → merge → verification history, plus `MERGE_POLICY_APPLIED`
  whenever the autopilot reconciles a row's merge policy.

---

## 8. Smoke check

With the app running:

1. Open Live Execution Center → **Автопилот рабочего стола**. Every switch is
   off; the panel says the wave appears after the first refresh.
2. Enable **Включить автопилот** only. Confirm `data/pipeline_settings.json` now
   contains `"enabled": true` and `"auto_launch": false`.
3. Add a task to the Execution Queue. On the next refresh the wave shows it as
   🟢 Назначено, with its agent and workspace, and
   `Автозапуск выключен — волна показана, но ничего не запускалось.`
4. Enable **Автозапуск готовых задач**. On the next refresh the task starts, and
   the row reports `✅ Запущено` with its run id.
5. Turn the master switch off. The next refresh plans nothing and launches
   nothing.

Automated equivalents live in `tests/test_independent_review.py` (the review
gate), `tests/test_autopilot_panel.py` (the panel),
`tests/test_task_pipeline.py` (planning and settings),
`tests/test_task_pipeline_concurrency.py` (duplicate ticks and exclusivity) and
`tests/test_task_pipeline_e2e.py` (the full
dependency wave → parallel launch → completion → merge → next wave scenario
against real git and a fake GitHub).

---

## 9. Files

| Path | Purpose |
|---|---|
| `data/pipeline_settings.json` | The persisted opt-in. Delete it to reset everything to off. |
| `data/task_pipeline.lock` | Host-wide tick lock. Released by the kernel if the holder dies; never needs manual cleanup. |
| `data/execution_queue.json` | The queue the autopilot plans from (unchanged by this feature). |
| `command_center/task_pipeline.py` | The tick. |
| `command_center/pipeline_settings.py` | The settings. |
| `command_center/ui/autopilot_panel.py` | The panel. |
