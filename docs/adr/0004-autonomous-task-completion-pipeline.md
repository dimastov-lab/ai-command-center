# ADR 0004 — Autonomous Task Completion Pipeline (AICC-AUTONOMY-001)

Status: **Accepted, implemented.**

## Context

Through ADR 0003, "is this agent actually running" became answerable from
`runtime.db`, reconciled against real OS processes. But the system stopped at
*process* completion. When a Claude run exited `COMPLETED`, `task_sync` marked
the Kanban task **"Needs Review"** and stopped — deliberately, because agents in
this codebase are sandbox-blocked from `git push`/`merge` (`agent_runner`'s
`--disallowedTools`) and can never reach a merged state on their own.

That left a real gap between two very different facts:

* **"Claude process finished"** — exit code 0, a report saved; and
* **"the engineering task is completed and merged into the target branch."**

Process completion is not task completion. A run can exit 0 while leaving
uncommitted changes; commit but never push; push with no PR; have its PR closed
without merging; have its remote branch deleted; be merged while local `main` is
stale; or "pass" while its tests actually fail. None of these were handled: the
task simply sat at "Needs Review" regardless of what really happened to the
change.

## Decision

Introduce a **deterministic, persisted, restart-safe completion pipeline** — a
*second* state machine, keyed on `run_id`, that runs **after** execution and is
distinct from (and additive to) the immovable `run.state` execution machine.

### 1. Three explicit state axes, one direction of derivation

| Axis | Owner | Meaning |
|---|---|---|
| `run.state` (`db.py`) | Supervisor | Execution lifecycle of the Claude subprocess; terminal once the process exits. |
| `completion_state` (`runtime.completion`) | Completion pipeline | Engineering lifecycle from "process finished" → "merged into target and verified". |
| Kanban `launch_status` (`models.LAUNCH_STATUSES`) | `task_sync` (projection) | Coarse, user-facing status derived from the two above. |

The completion pipeline begins **only** when a run reaches a terminal
`COMPLETED` execution state. It never moves a `run.state` and never invents a
new one — it keeps the frozen execution machine (and the launch-handshake
STARTING/RUNNING/STALE states from AICC-LAUNCH-HANDSHAKE-001) completely intact.

### 2. Completion state machine

```
EXECUTION_FINISHED → VALIDATING_RESULT → RESULT_VALID → PREPARING_PULL_REQUEST
  → PULL_REQUEST_OPEN → AWAITING_MERGE → MERGED → VERIFYING_TARGET_BRANCH → COMPLETED
```

Failure / intervention states: `VALIDATION_FAILED`, `PR_CLOSED_UNMERGED`,
`MERGE_BLOCKED`, `REQUIRES_ATTENTION`, `RECOVERY_PENDING`, `RECOVERY_FAILED`.

Terminal (never auto-advanced again): `COMPLETED` (success), and
`VALIDATION_FAILED` / `REQUIRES_ATTENTION` / `RECOVERY_FAILED` (await a human).

### 3. Evidence-based, never exit-code-based

`CompletionEvaluator.evaluate(task, run, repository_state, pull_request,
validation, policy)` is a **pure** function returning a structured
`CompletionAssessment` (status, action, reason_code, is_complete, is_recoverable,
requires_human, evidence, missing_requirements, recommended_action) — never a
bare boolean. A task is `COMPLETED` only when its change is **reachable from the
target branch**; every intermediate requirement (clean worktree, a real task
commit, validation passing, a remote branch, a PR, a merge) is checked
explicitly. Exit code 0 alone is never sufficient.

Two subtle rules the evaluator encodes:

* **"No task commit"** is `HEAD == base tip` (the run produced nothing) — kept
  distinct from a merged change, whose `HEAD` differs from the base tip while
  being reachable from it.
* **Squash-merge verification** checks whether the *merge commit* (the squash
  commit that lands on the target) is reachable — not whether the original
  commit hash survived, which a squash rewrites away.

### 4. Persistence — restart-safe by construction

Migration 5 adds three tables:

* `completion` — one mutable current-state row per run, compare-and-set-guarded
  by a `version` column exactly like `run`. Carries `task_id`, `repository_path`,
  `branch`, `base_branch`, `head_commit`, `remote_branch`,
  `pull_request_number`/`_url`/`_state`, `replaced_pull_request_number`/`_url`,
  `merge_commit`, `completion_state`, `last_reason_code`, `validation_summary`,
  `policy_json`, `last_checked_at`, `next_retry_at`, `retry_count`,
  `recovery_count`.
* `completion_validation` — one row per validation command per attempt, with
  **bounded** stdout/stderr summaries (never unlimited logs).
* `completion_event` — append-only audit trail (`EXECUTION_COMPLETED`,
  `VALIDATION_PASSED/FAILED`, `PR_CREATED`, `PR_CLOSED_UNMERGED`,
  `REPLACEMENT_PR_CREATED`, `PR_MERGED`, `TARGET_BRANCH_VERIFIED`,
  `TASK_COMPLETED`, …), ordered by a per-run `seq`, mirroring `run_event`.

The `completion` row (PK on `run_id`) is itself the idempotency guard: an
advance is a pure function of the persisted row plus freshly-observed git/GitHub
state, so a crash between any two steps resumes correctly, and a re-processed
terminal run never gets a second completion row (hence never a duplicate PR).

### 5. Adapters — the first privileged git/GitHub actor

Agents never push/merge; the pipeline is the *one* actor that may, and it runs
those in orchestration code, outside the agent sandbox:

* `runtime.repo_state` — read-only git inspection (commits-ahead, upstream,
  remote-branch existence, `merge-base --is-ancestor` reachability), all routed
  through the shared `git_info.run_git_command` primitive.
* `runtime.git_ops` — the narrow git *write* adapter (push / recreate branch),
  mirroring `portfolio_launch._run_git_write`. **Never force-pushes** (rejected
  before any subprocess runs).
* `runtime.github` — the first `gh` CLI integration: PR discovery, creation
  (body via `--body-file`, never interpolated into a shell), and merge.
  `PullRequestState` makes the critical distinction structural: **a closed PR is
  never treated as merged** just because its state is not OPEN.
* `runtime.validation` — configurable validation-plan execution. Commands come
  only from project/task config, run as fixed argv lists with `shell=False`, are
  restricted to an executable allowlist, and are captured as bounded summaries.

### 6. Merge policy — conservative by default

`CompletionPolicy` (resolved task-over-project-over-default): `merge_mode ∈
{manual, auto_after_checks, auto_after_checks_and_review}` (default **manual**),
`merge_method ∈ {squash, merge, rebase}` (default **squash**),
`allow_pr_recovery` (default **false**), `requires_pull_request` (default true),
`allow_local_only` (default false). In `manual` mode the pipeline tracks the PR
and waits at `AWAITING_MERGE` — it never auto-merges. Auto modes merge only when
checks pass (and, in the review variant, a required review is satisfied);
failing checks / conflicts / missing review yield `MERGE_BLOCKED`, never a merge.

### 7. Closed-unmerged recovery — idempotent, opt-in

For the exact scenario *local branch intact, remote branch deleted, PR CLOSED,
`mergedAt`/`mergeCommit` null*, and only when `allow_pr_recovery=true`, the
pipeline recreates the remote branch (idempotent push), opens a **replacement**
PR, links it to the closed one (`replaced_pull_request_number`), and persists
both. Recovery is idempotent because PR creation is always preceded by
discovery: a replacement that already exists (even across a restart mid-recovery)
is reused, never duplicated. With recovery disabled, the pipeline transitions to
`REQUIRES_ATTENTION` with a precise recommended action.

### 8. Supervisor integration — advances independently, never blocks the UI

The reconcile tick (`task_sync.reconcile_and_sync`, already driven every 2–5s)
**seeds** completion rows for newly-completed runs (cheap, DB-only) and
**projects** completion state onto the Kanban task. The actual advancement
(validation subprocesses, `gh` calls, push/merge) runs in
`Supervisor.advance_completions()`, invoked by an **opt-in background autopilot
thread** (`start_completion_autopilot`, enabled by `AICC_COMPLETION_AUTOPILOT`)
— off by default, so the plain Streamlit app never spawns a hidden scheduler and
the UI thread never blocks on validation or network. Advancement is bounded
(only *due*, non-terminal rows), uses exponential backoff via `next_retry_at`,
escalates transient GitHub/network failures to a retry (and permanent ones to
`REQUIRES_ATTENTION` after the retry cap), and never reprocesses terminal rows.

### 9. Task-status projection

Completion state maps onto the existing `models.LAUNCH_STATUSES`: validating →
"Running", PR open/merging/finalizing → "Needs Review", closed-unmerged /
validation-failed / blocked → "Requires Attention", verified-in-target →
"Completed" (with the task advanced to stage "Merged"/progress 100 and
`pull_request_status="merged"`, the previously-dormant field `recommend.py`
reads for dependency gating). No new Kanban status value is invented.

## Consequences

* **Positive**: a finished process is no longer conflated with a completed task.
  The Execution Center shows an explicit completion panel distinguishing
  "process finished" from "task completed and merged", including validation
  result, branch/commit, PR number + state, merge status, last-checked, and a
  recommended action.
* **Positive**: closed-unmerged PRs, stale locals, squash merges, and failed
  post-hoc validation are all handled deterministically and idempotently, and
  survive restarts.
* **Trade-off accepted**: live auto-advancement is opt-in (autopilot thread),
  honoring this codebase's "no hidden scheduler" stance and the "must not block
  the UI" requirement. With it off, completions are seeded and projected but not
  auto-driven.

## Known limitations

* Auto-advancement requires `AICC_COMPLETION_AUTOPILOT=1` (or an explicit
  `start_completion_autopilot()` call). Off by default.
* `gh` must be installed and authenticated for PR phases; when absent, PR-phase
  rows retry then escalate to `REQUIRES_ATTENTION`.
* A branch equal to the base branch (agent committed directly to the base) is
  flagged `REQUIRES_ATTENTION` rather than PR'd — no self-PR is attempted.
* Local-only completion (`requires_pull_request=false, allow_local_only=true`)
  completes after validation + a commit, without a remote/PR/merge — for local
  experiments; documented, not the default.
* Validation runs in the run's worktree with `PYTHONDONTWRITEBYTECODE=1`; a
  validator that writes other artifacts (e.g. `.pytest_cache`) is tolerated —
  the clean-tree check is enforced only *before* validation.

## Verification

`ruff check .`, `python -m compileall`, and `pytest` all clean. New coverage:
completion evaluator (evidence cases 1-8, 13-14), the DB layer (migration 5 +
CRUD/CAS), the four adapters, task-status projection, the UI completion panel
(cases 21-24), and end-to-end Scenario A/B/C plus the Recovery/Supervisor cases
(9-20) driven against **real git** with a fake GitHub client. Existing
launch-handshake, execution-queue, session-view, and AppTest suites remain
green.
