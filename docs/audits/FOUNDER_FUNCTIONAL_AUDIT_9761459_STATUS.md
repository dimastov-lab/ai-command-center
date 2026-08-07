# Founder Functional Audit 9761459 — Status

**CLOSED 2026-08-07**, merge-verified against `origin/main` @ `fb3da7f` the same day. This audit is
closed as an *audit*: every one of its 33 task candidates has been reconciled, classified and
either resolved or handed to a tracked roadmap row. Nothing from `9761459` is now carried only by
this document. The residual work is listed in §"Closure" below and lives in
`docs/roadmap/MASTER_ROADMAP_TASKS.json`, not here. Do not reopen this file to track progress —
track the `AICC-AUDIT-W*` rows.

Final disposition of the 14 rows the reconciliation left Still Open, all read on `origin/main`
(not on a task record, and not on an unpushed local branch): **7 merged**, **1 merged but still
partial** (W1-006), **1 folded** into the desktop D2 tasks (W3-002), **5 still open**. The seven
merged rows are now `Done` in the roadmap JSON. Detail: §"Merge verification".

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

Verified against `main` @ `744a09c` on 2026-08-07 by reading the code, not the task records — the
task records and the PR links proved unreliable in both directions (see the two warnings below).
Re-verified against the **merged** branch `origin/main` @ `fb3da7f` the same day — see
"Merge verification" below, which is the authoritative pass: `744a09c` is a *local* commit that
was never pushed.

Of the 14 rows the reconciliation left **Still Open**:

| Outcome | Count | Rows |
| --- | ---: | --- |
| Remediated and verified on `main` | 7 | W0-006, W1-004, W1-005, W1-007, W1-009, W2-004, W2-006 |
| Partially remediated — stays open | 1 | W1-006 (service half landed, no UI recovery action) |
| Folded into the desktop D2 stage tasks | 1 | W3-002 → `AICC-D2A`/`D2B`/`D2C`/`D2D` |
| Still open, re-verified unchanged | 5 | W1-002, W2-001, W2-002, W4-003, W4-004 |

Evidence commits, all confirmed ancestors of `main` @ `744a09c`: `7bfb025` (W0-006), `acdfe7c`
(W1-004), `8808fc5` + `0408c3e` (W1-005), `1eb942a` (W1-007), `ca4261d` (W1-009), `f6ff08f`
(W2-004), `b2b94f3` (W2-006), `8f4e3fc` (W1-006, partial). Per-row detail is in the
reconciliation's dated "Resolved since this reconciliation" / "Re-verified" notes.

### Re-verification pass, 2026-08-07 (`f2a9280` → `1a866ed` → `744a09c`)

The first closure pass ran against `f2a9280`. `main` then advanced by five commits, so every row
was re-read against `744a09c` before this document was finalized. One row changed:

- **W1-005 moved Still Open → Remediated.** `portfolio_launch.PROTECTED_BRANCH_NAMES`
  (`portfolio_launch.py:127`) plus a `casefold()` comparison in `_validate_branch_name` (line 214)
  now reject `main`/`master` — and `Main`/`MAIN`, which resolve to the same loose ref on a
  case-insensitive filesystem — on the real launch path, before any git call. The chain is
  `launch_portfolio_task` (line 1381) → `build_launch_plan` (line 757) → `resolve_branch`
  (called at line 797) → `_validate_branch_name` (called at line 262); the rejection becomes a
  blocker, and `launch_portfolio_task` returns at line 1407 on a non-launchable plan, so
  `create_worktree` (line 1283, called at line 1488) is never reached. Covered by
  `tests/test_portfolio_launch.py::test_launch_rejects_protected_branch_before_worktree_add`
  (line 936), which stubs `create_worktree` with an unconditional `pytest.fail`.

The other thirteen rows are unchanged — twelve were re-read against the code, and W3-002 is a
fold into `AICC-D2A`/`D2B`/`D2C`/`D2D` rather than a code claim. Three are worth noting:

- W1-009's `claude_cli_preflight` survives the `agent_runner.py` rewrites in `34d0798`/`1a866ed`
  and is still wired into the launcher at `app.py:682/722/808/988`.
- W1-006's `recover_stale_claim` (`portfolio_launch.py:514`) still has **no production call site**
  — the only caller in the tree is `tests/test_portfolio_launch.py:548` — so the row remains
  partial on exactly the same grounds.
- The newest commit, `744a09c`, is a launch-path change, but on the *task-v2* path
  (`launch_service.prepare_task_launch`'s workspace-isolation source repository), not the Portfolio
  path W1-005 guards. It adds nine lines to `launch_service.py` and touches no other file;
  `portfolio_launch.py` is unmodified by it, and branch validation is untouched.

Test evidence: `tests/test_launch.py`, `tests/test_git_info.py`,
`tests/test_runtime_report_path_containment.py`, `tests/test_report_path_containment.py`,
`tests/test_workspace_home_ui.py`, `tests/test_portfolio_launch.py` — **169 passed, 1 failed** on
`main` @ `744a09c` in a clean environment. The single failure is not an audit row:
`test_quick_action_launch_run_navigates_to_execution_center_prefilled`, the live
`reconcile_missing_run_links` regression recorded under "Defect found while closing" below.

A second test, `test_workspace_home_empty_state_all_six_unconfigured`, fails **only when the
developer's shell exports `AICC_REPORTS_ROOT`** — it is a test-isolation gap, not a code defect and
not an audit finding. The test does not neutralize an ambient `AICC_REPORTS_ROOT`, so a shell that
points it at the repository's own `reports/` (111 files here) leaks real reports into an assertion
that the reports panel shows its empty state. Isolated per-variable: with only `AICC_REPORTS_ROOT`
set it fails; with only `AICC_DATA_DIR` or only `AICC_BACKGROUND_SYNC` set it passes; with all three
unset it passes. `reports/` is gitignored (`.gitignore:35`), so CI and a clean checkout never see
it. Run this evidence set with the `AICC_*` variables stripped.

### Merge verification, 2026-08-07 (`origin/main` @ `fb3da7f`) — authoritative

Every prior pass in this document verified against the **local** `main`. That is not the same
branch. At the time of this pass the local checkout had **diverged** from the shared branch:

| | |
| --- | --- |
| Local `main` | `c41e9bd` — 2 commits *not* pushed (`744a09c`, `c41e9bd`) |
| `origin/main` | `fb3da7f` — 8 commits *not* present locally, incl. the merge of PR #141 |
| Relationship | diverged; neither is an ancestor of the other |

So "verified on `main`" in the sections above means "verified on a local branch that no one else
can see". This pass re-reads every row on `origin/main` @ `fb3da7f`, which is what "merged"
actually means. **The verdict does not change** — all nine evidence commits
(`7bfb025`, `acdfe7c`, `8808fc5`, `0408c3e`, `1eb942a`, `ca4261d`, `f6ff08f`, `b2b94f3`,
`8f4e3fc`) are confirmed ancestors of `origin/main`, and each row's *code* was re-read there
rather than inferred from the commit being present:

| Row | Outcome | Read on `origin/main` |
| --- | --- | --- |
| W0-006 | Merged | `runtime/reports.py:_safe_path_component` sanitizes the `project` component inside `report_path_for` (line 56) |
| W1-004 | Merged | one acknowledgement per warning keyed by `code`; `unacknowledged_warning_codes` gates the button (`app.py:903-977`) |
| W1-005 | Merged | `portfolio_launch.PROTECTED_BRANCH_NAMES` (line 127) + `casefold()` in `_validate_branch_name` (line 214) |
| W1-007 | Merged | two-step delete confirmation in `app.py` (`*_delete_confirm_open`, 10 references) |
| W1-009 | Merged | `agent_runner.claude_cli_preflight` (line 271), wired at `app.py:682/722/808/988` |
| W2-004 | Merged | `render_project_planning_intelligence` (`app.py:3535`), called at 3596 and 5117 |
| W2-006 | Merged | `git_info.fetch_remotes` (line 122) + `get_ahead_behind` (line 133) |
| W1-006 | Merged, still partial | `recover_stale_claim` (`portfolio_launch.py:514`) — still **no production call site**; the only caller on `origin/main` is `tests/test_portfolio_launch.py:548` |
| W1-002 | Still open | `scripts/start-task.sh` still accepts only `AIOS`, `BANK`/`BANK_STRATEGY`, `LEGAL` |
| W2-001 | Still open | no task-schema module under `command_center/` |
| W2-002 | Still open | no cycle detection on any `tasks_repository` write path; cycles remain a read-only `break_cycle` recommendation in `portfolio_intelligence` |
| W4-003 | Still open | run→Timeline collection is still opt-in behind `AICC_BACKGROUND_SYNC` (`task_pipeline.start_background_sync`, line 2286) |
| W4-004 | Still open | no founder batch-confirmation surface |
| W3-002 | Folded | into `AICC-D2A`/`D2B`/`D2C`/`D2D`; not a code claim |

Test evidence, run in a detached worktree at `origin/main` @ `fb3da7f` with the ambient `AICC_*`
variables stripped and `AICC_DATA_DIR`/`AICC_REPORTS_ROOT` redirected to temp dirs:
`tests/test_launch.py`, `tests/test_git_info.py`, `tests/test_runtime_report_path_containment.py`,
`tests/test_report_path_containment.py`, `tests/test_workspace_home_ui.py`,
`tests/test_portfolio_launch.py` — **170 passed, 0 failed**. The single failure recorded in the
previous pass is gone; see "Defect found while closing" for why.

`docs/roadmap/MASTER_ROADMAP_TASKS.json` was updated by this pass: the seven merged rows moved
`Backlog` → `Done` with their evidence recorded in `ready_reason`. Before this, all 13
`AICC-AUDIT-W*` rows read `Backlog` regardless of what had shipped, so the roadmap — the tracker
this document hands residual work to — carried no signal on this track either. Six rows remain
`Backlog` and are genuinely open (the five above plus W1-006, which is partial).

**Carry-over, not an audit row:** the local-only commit `744a09c` ("use task-level
`repository_path` for workspace isolation when workspace comes from task", 9 lines in
`launch_service.py`) is **not on `origin/main`** — `prepare_task_launch` there still reads
`source_repository_path` from the project config only. Anything describing that fix as delivered
is describing the local checkout. It needs to be pushed or re-landed.

### Warning 1 — a merged PR is not proof, and a closed PR is not disproof

Three rows contradict their own task records, in opposite directions:

- **W0-006** is fixed on `main` (`7bfb025`) even though its PR **#84 was closed unmerged**. The
  fix arrived by a different commit.
- **W1-005** is fixed on `main` (`8808fc5`, `0408c3e`) even though its PR **#85 was closed
  unmerged** — the same shape as W0-006, and the reason this row flipped between the two closure
  passes. `data/tasks.json` still shows it `In Progress` against that dead PR.
- **W1-004** is fixed and merged (PR **#88**), but `data/tasks.json` still has
  `AICC-AUDIT-W1-004` in `Backlog`.

Consequence for the next audit: verify rows by reading `main`, and treat
`pull_request_url` on a task record as a lead, never as evidence. Two of the three counterexamples
are now "closed PR, shipped anyway", so a closed PR is the weaker signal of the two.

### Warning 2 — the live task store is stale for this track

`data/tasks.json` holds only **7** of the 13 `AICC-AUDIT-W*` rows, and only **2 of those 7** —
`AICC-AUDIT-W2-001` and `AICC-AUDIT-W4-003`, both `Backlog` and both genuinely still open — are
both correct and read correctly. The other five are wrong or misleading (re-checked against the
live store on 2026-08-07 at `744a09c`; the store is unchanged, so all five still hold).

Three carry a **status that contradicts `main`** — all three understate the work, and all three
sit in a state that says "not done" for a row that is:

- `AICC-AUDIT-W1-005` — `In Progress`, linking PR #85, which is **closed unmerged**. As of
  `744a09c` the fix has landed by another route (`8808fc5`, `0408c3e`), so this row is wrong in
  both directions at once: it is not in progress, it is done.
- `AICC-AUDIT-W1-004` — `Backlog`, but shipped and merged (PR #88).
- `AICC-AUDIT-W0-006` — `Backlog`, but fixed on `main` (`7bfb025`). The same shape as W1-004 and
  the one this document's earlier drafts missed: its PR #84 is closed unmerged, so *both* the
  status field and the PR link read as "nothing shipped" while the fix is on `main`.

Two more are **correct but read as failed**:

- `AICC-AUDIT-W1-007` and `AICC-AUDIT-W2-004` — `Done` and correct, but both carry a
  `regressed_after_done` timeline event and `launch_status: "Requires Attention"` from the
  completion pipeline, so they read as failed in the UI while being genuinely delivered.

`launch_status: "Requires Attention"` is in fact set on **six of the seven** rows (every one except
`AICC-AUDIT-W2-001`), including the three `Backlog` rows, so on this track that field carries no
signal at all.

The six rows absent from the store (W1-002, W1-006, W1-009, W2-002, W2-006, W4-004) exist only in
the roadmap JSON. Reconciling the store against the roadmap is part of `AICC-GOV-F2`.

### Defect found while closing (not an audit row) — RESOLVED on `origin/main`

**Resolved 2026-08-07 on `origin/main` @ `fb3da7f`.** `command_center/execution_queue.py:907`
defines `reconcile_missing_run_links` again — restored by `b2134c4` ("fix(queue): add
reconcile_missing_run_links to backfill lost run_id", +33 lines), which reached the shared branch
through the PR #141 merge. The call site at `app.py:3339` is unchanged and now resolves, so the
Live Execution Center no longer raises. This is why the evidence set is 170 passed / 0 failed on
`origin/main` where it was 169/1 on the local branch:
`tests/test_workspace_home_ui.py::test_quick_action_launch_run_navigates_to_execution_center_prefilled`
passes there.

The crash is still reproducible on the **local** `main` (`c41e9bd`), which predates the fix — the
local branch defines the name nowhere under `command_center/` while `app.py:3339` still calls it.
That is a symptom of the divergence recorded under "Merge verification", not a live defect on the
shared branch, and it clears when the local branch is brought up to date. No separate task is
needed.

The original finding, kept for the record:

`app.py:3339` calls `execution_queue.reconcile_missing_run_links(...)`, which **no longer exists**:
commit `81833da` ("load-aware executor selection") deliberately removed it from
`command_center/execution_queue.py` but left this call site behind. Rendering the Live Execution
Center therefore raises `AttributeError`. Reproduced by
`tests/test_workspace_home_ui.py::test_quick_action_launch_run_navigates_to_execution_center_prefilled`.
This is a live page-crashing regression on `main` and is independent of the audit; it needs its own
task.

~~**Still live at `744a09c` (re-checked 2026-08-07).**~~ The five commits added since the first
closure pass did not touch it: the name is defined nowhere under `command_center/`, and
`app.py:3339` remains its only reference in that tree. It reproduces as an `AttributeError` raised
from `_render_live_execution_center_body` (`app.py:3339`) via `render_live_execution_center`
(`app.py:3484`). **Superseded** — this held only for the local branch; `b2134c4` on `origin/main`
restores the function, as recorded at the head of this section.

## Source-of-truth warning

This historical audit must not be treated as the current source of truth. Its task candidates
were reconciled against `origin/main` `5eed19c` and last re-verified against `origin/main`
`fb3da7f` (see "Merge verification"; the earlier `744a09c` pass ran on an unpushed local branch) — read
`FOUNDER_FUNCTIONAL_AUDIT_9761459_RECONCILIATION.md` instead of this document's findings for
the current state of each row. The findings *narrative* in
`FOUNDER_FUNCTIONAL_AUDIT_9761459.md` has not been re-audited and remains a `9761459` snapshot.
