# AI Command Center — Current State

Updated: 2026-07-23

## 0. AI Command Center platform

Status: Active, local Streamlit implementation

Current position:
- `app.py` hosts the implemented 19-destination Streamlit control application.
- `data/tasks.json` remains the planning and Kanban store.
- `data/runtime.db` is authoritative for asynchronous execution and completion state.
- Execution Center provides process supervision, streaming events, cancellation, timeouts and
  restart reconciliation; live process handles remain owned by the hosting Python process.
- Portfolio Execution and Portfolio Overview provide guarded worktree launch plus read-only
  dependency, health, capacity and recommendation views.
- The persisted completion pipeline supports validation, push, pull-request and merge workflows;
  completion autopilot and automatic merge policies are opt-in and disabled by conservative
  defaults.
- Legacy synchronous and JSON/JSONL paths remain present alongside SQLite.

Current boundaries:
- Normal task launches require explicit user action; no general autonomous task scheduler is
  implemented.
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
