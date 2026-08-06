# AI Command Center — Current State

Updated: 2026-08-07

## 0. AI Command Center platform

Status: Active, local Streamlit implementation

Current position:
- `app.py` hosts the implemented Streamlit control application: 20 page handlers, 16 shown in the
  sidebar (chat, generated files, reports, and context open inside the project view).
- `data/tasks.json` is the default planning and Kanban store. `AICC_TASKS_BACKEND=aios` routes
  all task reads/writes through the AIOS Tasks API instead (requires `AICC_AIOS_URL` + `AICC_AIOS_TOKEN`).
  `tasks_repository.get_repository()` is the factory; `scripts/migrate_tasks_to_aios.py` provides
  one-shot migration. See CHANGELOG [Unreleased] §"AIOS Tasks backend (Sprint 4)" for limitations.
- `data/runtime.db` schema 11 is authoritative for asynchronous execution, completion, the
  persisted autonomy-proposal lifecycle, the execution-provider fields, the independent-review
  verdict, and the `queue_entry` mirror (ADR 0007 dual-write).
- Execution Center provides process supervision, streaming events, cancellation, timeouts and
  restart reconciliation; live process handles remain owned by the hosting Python process.
  Process-group supervision is fail-closed to POSIX `waitid(WNOWAIT)`, keeps the launch-time PGID
  pinned until descendants are drained, and separates OS exit from terminal-state/report
  finalization so slow persistence cannot cause a false timeout or late signal.
- Normal task-v2 launches require explicit confirmation before any provisioning, may create an
  isolated worktree offline, and fail closed unless source repository, expected branch, worktree
  isolation and configured status policy verify before process launch.
- Application-owned execution-queue mutations hold a same-host cooperative OS advisory lock across
  the complete persisted read-modify-write cycle; raw queue primitives and lock-free reads remain.
- `ExecutionCenterAPI.plan_schedule` provides deterministic, explainable, read-only scheduling
  decisions. It creates no durable claim, queue entry or run and has no background driver.
- The autonomy proposal domain/API persists evidence, policy, approval and dispatch-boundary state.
  An operator approve/reject inbox (`ui/proposals_panel.py`) surfaces and decides proposals, but
  there are no automated evidence collectors, project policy resolver, background driver or executor.
- Portfolio Execution and Portfolio Overview provide guarded worktree launch plus read-only
  dependency, health, capacity and recommendation views.
- The persisted completion pipeline supports validation, push, pull-request and merge workflows;
  completion autopilot and automatic merge policies are opt-in and disabled by conservative
  defaults.
- Checked-in CI validates committed-diff whitespace, Ruff, byte compilation and pytest on Python
  3.14. Informational, non-blocking `mypy` and coverage steps run alongside the deterministic
  quartet but do not gate the merge.
- Runtime history retention is **off by default**: `AICC_RUNTIME_RETENTION_DAYS=<N>` prunes
  `run_event` rows for terminal runs older than `N` days on startup, and
  `AICC_RUNTIME_VACUUM_ON_START=1` reclaims disk with `VACUUM` afterward.
- `data/chats.json` and `data/activity.jsonl` remain active application stores alongside SQLite;
  legacy synchronous execution and the `data/runs.jsonl` journal also remain present.
- Founder Functional Audit `9761459` is **closed** (2026-08-07). Of its 14 Still Open rows, 6 are
  remediated and verified on `main` (report-path containment, per-warning launch acknowledgement,
  task-delete confirmation, `claude` pre-flight, Workspace Home intelligence, git ahead/behind +
  fetch), 1 is partial (Portfolio stale-claim recovery: service half only), 1 is folded into the
  desktop D2 stage tasks, and 6 remain open as `AICC-AUDIT-W*` rows in
  `docs/roadmap/MASTER_ROADMAP_TASKS.json`. See
  `docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` §Closure.

Current boundaries:
- Six audit remediations remain outstanding and are the known functional gaps: `scripts/start-task.sh`
  accepts 3 of the 11 registered project ids; Portfolio has no protected-branch guard before
  `git worktree add`; there is no canonical task schema and no dependency-cycle detection; run
  results reach a task's Timeline only page-driven or under `AICC_BACKGROUND_SYNC`; and the
  autopilot has no founder batch-confirmation surface before it launches.
- `data/tasks.json` is out of sync with the roadmap for the audit-remediation track: it holds 7 of
  13 `AICC-AUDIT-W*` rows and three of those contradict `main`. Reconciliation is tracked as
  `AICC-GOV-F2`; a refreshed audit against current `main` is tracked as `AICC-GOV-F4B`.
- **Known regression on `main`:** `app.py:3339` calls the removed
  `execution_queue.reconcile_missing_run_links`, so rendering the Live Execution Center raises
  `AttributeError`. Introduced by `81833da`; untracked as of this update.
- Normal task launches require explicit user action. Scheduler `ASSIGN` results are point-in-time
  advice, not persisted claims; task-id/capacity decisions may race before the separate launch, and
  only exact-workspace exclusion is enforced transactionally by the runtime launch path.
- Fail-closed workspace verification is scoped to normal task-v2 callers that supply
  `WorkspaceSpec`; low-level/ad-hoc launches preserve their separate behavior.
- The current private-repository plan does not expose branch protection/rulesets, so CI is
  automatic but required-check enforcement remains an operator merge discipline.
- Git worktree creation, push, pull-request creation and merge are privileged capabilities with
  confirmation or policy safeguards.
- The native PySide6 desktop client remains documentation and design work only.
- The runtime is local and process-hosted, not a distributed or production-ready worker platform.

## 1. AIOS

Status: Active

Current position:
- P0 completed.
- P1 API, authentication, OpenAPI contract and SDK work in progress.
- Product, specifications, architecture and commercial streams run in parallel.

Current priority:
- Complete the active P1 development sequence.
- Keep one agent = one worktree = one branch.
- Require independent review before commit and merge.

Next decision:
- Confirm the exact next P1 task from the current repository state.

---

## 2. Bank Strategy

Status: Active

Current objective:
- Implement the new process across the entire organization.
- Provide the Management Board with measurable monthly implementation control.

Current priority:
- Finalize one executive slide.
- Define 30/60/90-day milestones.
- Define monthly metrics, owners, deviations and corrective actions.

Next deliverable:
- Board implementation and execution-control dashboard.

---

## 3. Legal

Status: Active

Current areas:
- Claim and supporting documents.
- Criminal-case evidence.
- Correspondence and payment analysis.
- Evidence preservation and procedural submissions.

Operating rule:
- Separate evidence extraction, calculations, legal analysis and document drafting.

---

## 4. Business

Status: Active

Current areas:
- Open Book logistics model.
- Investment and loan documentation.
- Corporate and counterparty matters.

---

## 5. Personal

Status: Ongoing

Use for:
- Personal communications.
- Automotive issues.
- Scheduling and household tasks.

---

# Global Operating Rules

1. One project must not be mixed with another project.
2. One agent = one task = one branch = one worktree.
3. Every technical implementation requires independent review.
4. No commit, push, pull-request creation or merge without explicit authorization.
5. Every task must have a measurable Definition of Done.
6. Closed architectural decisions must not be reconsidered without new evidence.
7. Current-state files are the primary source of project context.
