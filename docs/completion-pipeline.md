# Autonomous Task Completion Pipeline — Operator Guide

This is the operator-facing companion to
[ADR 0004](adr/0004-autonomous-task-completion-pipeline.md). It explains the
distinction the pipeline enforces, how to configure it, and what to do when a
task lands in **Requires Attention**.

## Process completion ≠ task completion

When a Claude run exits successfully, the *process* is finished — but the
*engineering task* is not necessarily done. The completion pipeline determines,
from evidence, whether the change actually reached the target branch:

* Claude may exit 0 but leave **uncommitted** changes.
* It may commit but never **push**.
* The branch may be pushed with **no PR**.
* A PR may be **closed without merging**.
* The remote branch may be **deleted** while the local commit remains.
* A PR may be **merged** while local `main` is stale.
* Post-hoc **validation** (tests/lint) may fail.

The Execution Center completion panel always shows both: the process status
*and* the completion status ("Process finished" vs "Done").

## Completion lifecycle

```
EXECUTION_FINISHED → VALIDATING_RESULT → RESULT_VALID → PREPARING_PULL_REQUEST
  → PULL_REQUEST_OPEN → AWAITING_MERGE → MERGED → VERIFYING_TARGET_BRANCH → COMPLETED
```

Intervention states: `VALIDATION_FAILED`, `PR_CLOSED_UNMERGED`, `MERGE_BLOCKED`,
`REQUIRES_ATTENTION`, `RECOVERY_PENDING`, `RECOVERY_FAILED`.

The pipeline auto-advances only the non-terminal states, with exponential
backoff. `COMPLETED`, `VALIDATION_FAILED`, `REQUIRES_ATTENTION`, and
`RECOVERY_FAILED` are terminal and never reprocessed.

## Enabling auto-advancement

Advancement (validation subprocesses, `gh` calls, push/merge) is **off by
default** so the UI never blocks and nothing auto-merges without opt-in. Enable
the background autopilot per backend process:

```bash
export AICC_COMPLETION_AUTOPILOT=1
```

With it off, completion rows are still **seeded** and their state **projected**
onto the task, but nothing advances automatically.

`gh` must be installed and authenticated (`gh auth status`) for PR phases.

## Configuration (`data/project_config.json`, per project)

| Key | Default | Meaning |
|---|---|---|
| `merge_mode` | `manual` | `manual` / `auto_after_checks` / `auto_after_checks_and_review` |
| `merge_method` | `squash` | `squash` / `merge` / `rebase` |
| `allow_pr_recovery` | `false` | Auto-recreate branch + open replacement PR for a closed-unmerged PR |
| `requires_pull_request` | `true` | Whether the task must be merged via a PR |
| `allow_local_only` | `false` | Complete after validation + commit, without remote/PR/merge |
| `validation_required` | `true` | Run the validation plan before opening a PR |
| `validation_commands` | `null` | List of commands (e.g. `["pytest", "ruff check ."]`); `null` = safe default (byte-compile) |
| `max_retries` | `20` | Backoff cap before a stuck state escalates to Requires Attention |

Task-level keys of the same name override the project-level ones.

Validation commands are argv-executed with `shell=False`, restricted to an
allowlist of validators (`pytest`, `ruff`, `mypy`, `python3`, `make`, `npm`, …).

## Merge safety (never violated)

* Never force-pushes.
* Never deletes branches (before or after verification).
* Never merges when required checks are failing or a required review is absent.
* Never treats a closed PR as merged.
* Never treats exit code 0 as task completion.
* All automatic actions are idempotent.

## Operator actions for **Requires Attention**

The completion panel shows a **recommended action** and reason code. Common cases:

| Reason | What happened | Do this |
|---|---|---|
| `UNCOMMITTED_CHANGES` | Agent left the worktree dirty | Review the worktree; commit or discard, then re-run the task |
| `NO_TASK_COMMIT` | The run produced no commit | Re-run the task; the agent did no committed work |
| `VALIDATION_FAILED` | Validation commands failed | Inspect the recorded validation output; fix and re-run |
| `RECOVERY_DISABLED` | PR closed unmerged; recovery off | Set `allow_pr_recovery=true` to auto-recover, or recreate the PR manually |
| `RECOVERY_NOT_POSSIBLE` | PR closed unmerged; local work gone | Recover manually — the branch no longer contains the change |
| `TARGET_NOT_VERIFIED` | GitHub reported a merge not visible in the target after repeated checks | Fetch and verify manually; the merge may not have propagated |
| `CHECKS_FAILING` / `PR_CONFLICTING` | Auto-merge blocked by CI or conflicts | Fix CI / resolve conflicts, or merge manually |

Every completion event (validation, PR created, closed-unmerged, replacement PR,
merge, target verified) is recorded in `completion_event` with timestamps and
metadata — no credentials or environment are ever stored.

## Try it

```bash
python3 scripts/demo_completion_pipeline.py
```

Runs the three demonstration scenarios (normal completion, closed-PR recovery,
validation failure) against a real throwaway git repo and a fake GitHub client.
