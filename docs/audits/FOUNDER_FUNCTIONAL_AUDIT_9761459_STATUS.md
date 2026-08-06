# Founder Functional Audit 9761459 — Status

**CLOSED 2026-08-07.** This audit is closed as an *audit*: every one of its 33 task candidates has
been reconciled, classified and either resolved or handed to a tracked roadmap row. Nothing from
`9761459` is now carried only by this document. The residual work is listed in §"Closure" below
and lives in `docs/roadmap/MASTER_ROADMAP_TASKS.json`, not here. Do not reopen this file to track
progress — track the `AICC-AUDIT-W*` rows.

## Audit baseline

- Audited HEAD: `9761459`
- Verdict: `READY AFTER REMEDIATION`
- Audit findings: 26
  - Blocker: 4
  - Major: 9
  - Minor: 10
  - Nit: 3
- Generated task candidates: 33

## Current status

This audit describes the repository state at commit `9761459`.

PR #9 was merged after this audit:

- Feature commit: `98d7714`
- Merge commit: `4447619`
- Scope: transactional task import and shared task-storage locking

Some findings from the audit may therefore already be resolved, partially resolved, superseded, or require reassessment against the current `main`.

## Task package status

`FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json` is preserved as a historical input artifact.

It has not been imported into `data/tasks.json`.

The package is not currently compatible with the active `task_import.py` schema. Before importing, it requires:

- `dependencies` mapping to `depends_on`;
- `worktree_branch` mapping to `branch`;
- addition or resolution of `repository_path`;
- addition or resolution of `workspace_path`;
- validation of all current project identifiers;
- triage against the current implementation state.

## Required next steps

1. ~~Compare all 33 task candidates with the current `main`.~~ Done — see
   `FOUNDER_FUNCTIONAL_AUDIT_9761459_RECONCILIATION.md` (reconciled against `origin/main`
   `5eed19c`, after the CI and launcher integration gates).
2. ~~Classify every candidate as `Done` / `Still Open` / `Superseded` / `Duplicate`.~~ Done —
   14 Done, 14 Still Open, 4 Superseded, 1 Duplicate.
3. ~~Convert only current and approved tasks to the active task-import schema.~~ Done —
   `FOUNDER_AUDIT_9761459_STILL_OPEN_IMPORT_PACKAGE.json` carries the 14 Still Open rows,
   mapped to the canonical `AICC` project id. Validated with
   `scripts/import_tasks.py --dry-run` against an isolated data dir: 14 new, 0 errors,
   0 warnings.
4. ~~Import the converted package through the canonical task-import mechanism.~~ Superseded — the
   14 Still Open rows were folded into `docs/roadmap/MASTER_ROADMAP_TASKS.json` as 13 executable
   `AICC-AUDIT-W*` rows (W3-002 folded into `AICC-D2A`/`D2B`/`D2C`/`D2D`), and 7 of them were
   also created in the live `data/tasks.json` and run. The standalone package import is no longer
   the path; the roadmap is.
5. **Open:** run a refreshed Founder Functional Audit against the current `main`. Tracked as
   `AICC-GOV-F4B` ("Audit-closure gate: refreshed Founder Audit against main"), which carries all
   13 rows as its evidence set.

## Closure

Verified against `main` @ `f2a9280` on 2026-08-07 by reading the code, not the task records — the
task records and the PR links proved unreliable in both directions (see the two warnings below).

Of the 14 rows the reconciliation left **Still Open**:

| Outcome | Count | Rows |
| --- | ---: | --- |
| Remediated and verified on `main` | 6 | W0-006, W1-004, W1-007, W1-009, W2-004, W2-006 |
| Partially remediated — stays open | 1 | W1-006 (service half landed, no UI recovery action) |
| Folded into the desktop D2 stage tasks | 1 | W3-002 → `AICC-D2A`/`D2B`/`D2C`/`D2D` |
| Still open, re-verified unchanged | 6 | W1-002, W1-005, W2-001, W2-002, W4-003, W4-004 |

Evidence commits, all confirmed ancestors of `main` @ `f2a9280`: `7bfb025` (W0-006), `acdfe7c`
(W1-004), `1eb942a` (W1-007), `ca4261d` (W1-009), `f6ff08f` (W2-004), `b2b94f3` (W2-006),
`8f4e3fc` (W1-006, partial). Per-row detail is in the reconciliation's dated
"Resolved since this reconciliation" / "Re-verified" notes.

Test evidence: `tests/test_launch.py`, `tests/test_git_info.py`,
`tests/test_runtime_report_path_containment.py`, `tests/test_report_path_containment.py`,
`tests/test_workspace_home_ui.py` — 63 passed, 1 failed on `main`. The single failure is unrelated
to the audit rows and is recorded under "Defect found while closing" below.

### Warning 1 — a merged PR is not proof, and a closed PR is not disproof

Two rows contradict their own task records, in opposite directions:

- **W0-006** is fixed on `main` (`7bfb025`) even though its PR **#84 was closed unmerged**. The
  fix arrived by a different commit.
- **W1-004** is fixed and merged (PR **#88**), but `data/tasks.json` still has
  `AICC-AUDIT-W1-004` in `Backlog`.

Consequence for the next audit: verify rows by reading `main`, and treat
`pull_request_url` on a task record as a lead, never as evidence.

### Warning 2 — the live task store is stale for this track

`data/tasks.json` holds only **7** of the 13 `AICC-AUDIT-W*` rows, and three of those seven
disagree with `main`:

- `AICC-AUDIT-W1-005` — `In Progress`, linking PR #85, which is **closed unmerged**; no code
  landed. It is not in progress.
- `AICC-AUDIT-W1-004` — `Backlog`, but shipped and merged.
- `AICC-AUDIT-W1-007` and `AICC-AUDIT-W2-004` — `Done` and correct, but both carry a
  `regressed_after_done` timeline event and `launch_status: "Requires Attention"` from the
  completion pipeline, so they read as failed in the UI while being genuinely delivered.

The six rows absent from the store (W1-002, W1-006, W1-009, W2-002, W2-006, W4-004) exist only in
the roadmap JSON. Reconciling the store against the roadmap is part of `AICC-GOV-F2`.

### Defect found while closing (not an audit row)

`app.py:3339` calls `execution_queue.reconcile_missing_run_links(...)`, which **no longer exists**:
commit `81833da` ("load-aware executor selection") deliberately removed it from
`command_center/execution_queue.py` but left this call site behind. Rendering the Live Execution
Center therefore raises `AttributeError` on current `main`. Reproduced by
`tests/test_workspace_home_ui.py::test_quick_action_launch_run_navigates_to_execution_center_prefilled`.
This is a live page-crashing regression on `main` and is independent of the audit; it needs its own
task.

## Source-of-truth warning

This historical audit must not be treated as the current source of truth. Its task candidates
were reconciled against `origin/main` `5eed19c` and re-verified against `main` `f2a9280` — read
`FOUNDER_FUNCTIONAL_AUDIT_9761459_RECONCILIATION.md` instead of this document's findings for
the current state of each row. The findings *narrative* in
`FOUNDER_FUNCTIONAL_AUDIT_9761459.md` has not been re-audited and remains a `9761459` snapshot.
