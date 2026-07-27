# Desktop Autopilot — Claude Code handoff

## Current checkpoint

Worktree: `/Users/dmitrijcernikov/Projects/ai-command-center-pr34-remediation`  
Branch: `codex/pr34-lifecycle-remediation`

The supervisor lifecycle remediation is implemented and covered by:

```bash
/Users/dmitrijcernikov/Projects/ai-command-center-ci/.venv/bin/python -m pytest -q \
  tests/test_runtime_supervisor.py \
  tests/test_runtime_reconciliation.py \
  tests/test_runtime_process_tree.py
```

Last result: `96 passed`.

The original checkout at `/Users/dmitrijcernikov/Projects/ai-command-center`
contains the live ignored `data/tasks.json` and an unrelated untracked
`FETCH_HEAD`. Do not modify its Git state. Task-store writes must use
`tasks_repository` / `task_import`, never direct JSON replacement.

## Product objective

Deliver a working desktop pipeline:

1. rank eligible tasks;
2. show the next parallel-safe wave;
3. automatically launch explicitly opted-in queued tasks;
4. supervise execution without duplicate task/workspace attempts;
5. validate, push, open a PR, and merge only after configured gates;
6. project completion back to Kanban;
7. immediately recompute the next wave.

## Existing components to reuse

- Ranking: `command_center.recommend`.
- Rich recommendation view: `command_center.recommendation_service`.
- Dependency-ready queue: `command_center.execution_queue`.
- Capacity/workspace planner: `command_center.runtime.scheduler`.
- Confirmed launch: `ExecutionCenterAPI.start_run` via
  `execution_queue.launch_ready`.
- Runtime ownership: `command_center.runtime.supervisor`.
- Validation/PR/merge: `command_center.runtime.completion_service`.
- Kanban projection: `command_center.runtime.task_sync`.
- Persistent task writes: `command_center.tasks_repository`.

Do not create a second queue, scheduler, launcher, task store, or completion
state machine.

## Missing integration

- Scheduler decisions are read-only and are not mapped back to queue entry IDs.
- Queue launch is button-only; there is no bounded persistent dispatcher.
- Completion autopilot is optional and independent from queue dispatch.
- Default completion policy is manual merge.
- Recommendations show top tasks but not the capacity/workspace-safe parallel
  wave or deferral reasons.
- No single idempotent tick performs:
  reconcile → completion advance → task sync → queue readiness → plan → launch.

## Implementation roadmap

### Wave A — orchestration foundation

- `AICC-DESKTOP-001`: add a Streamlit-free `task_pipeline` service with one
  bounded, lock-protected tick.
- `AICC-DESKTOP-002`: adapt READY queue entries and their latest run history
  into `scheduler.WorkItem`.
- `AICC-DESKTOP-003`: map scheduler decisions back to queue entry IDs and
  return an auditable `PipelineTickResult`.
- `AICC-DESKTOP-004`: add persistent opt-in settings: enabled, auto-launch,
  auto-merge-after-checks, max global concurrency, max agent concurrency.

Dependencies: 002 → 003 → 001; 004 → 001.  
Parallel work: 002 and 004.

### Wave B — safe dispatcher

- `AICC-DESKTOP-005`: serialize plan-and-dispatch with a same-host advisory
  pipeline lock.
- `AICC-DESKTOP-006`: launch only `ASSIGN` decisions through
  `execution_queue.launch_ready`; preserve dirty/sensitive/worktree gates.
- `AICC-DESKTOP-007`: make concurrent/repeated ticks idempotent for task ID,
  queue entry, workspace, and attempt.
- `AICC-DESKTOP-008`: isolate one failed launch so other assigned entries in
  the wave still start.

Dependencies: Wave A.  
Parallel work: 005 and 008; then 006 and 007.

### Wave C — completion and next-wave loop

- `AICC-DESKTOP-009`: advance completion rows before and after task sync so a
  newly seeded completion can progress on the same tick.
- `AICC-DESKTOP-010`: apply auto-merge only for an explicit persisted opt-in;
  keep checks/review/conflict gates authoritative.
- `AICC-DESKTOP-011`: after verified merge, persist Kanban completion,
  reevaluate dependencies, and return the newly available wave.
- `AICC-DESKTOP-012`: ensure transient GitHub/git failures back off without
  blocking unrelated tasks.

Dependencies: Wave B.  
Parallel work: 009, 010, and 012; then 011.

### Wave D — desktop UI

- `AICC-DESKTOP-013`: show the next parallel wave with priority, assigned
  agent, workspace, and rationale.
- `AICC-DESKTOP-014`: show DEFER/BLOCKED reason codes and remediation.
- `AICC-DESKTOP-015`: add explicit persistent autopilot and auto-merge
  controls; default disabled.
- `AICC-DESKTOP-016`: run the bounded tick from the existing cached
  `ExecutionCenterAPI` refresh path, never from a second supervisor.
- `AICC-DESKTOP-017`: add durable flash/audit output for launches, skips,
  completion progress, and merge results.

Dependencies: Wave C.  
Parallel work: 013, 014, and 015; then 016 and 017.

### Wave E — release gates and live roadmap

- `AICC-DESKTOP-018`: unit tests for work-item adaptation, ordering, capacity,
  retry history, and decision-to-entry mapping.
- `AICC-DESKTOP-019`: concurrency tests for duplicate ticks and workspace/task
  exclusion.
- `AICC-DESKTOP-020`: end-to-end fake-Claude scenario:
  dependency wave → parallel launch → completion → merge → next wave.
- `AICC-DESKTOP-021`: desktop smoke test and operator documentation.
- `AICC-DESKTOP-022`: generate a validated task-import package for these IDs,
  dry-run it against the live store, then import via the canonical CLI.

Dependencies: Waves A–D.  
Parallel work: 018, 019, and 021; 020 is the final gate; 022 follows validation.

## Required invariants

- Autopilot and auto-merge are persistent explicit opt-ins.
- Sensitive-content confirmation and workspace verification are never bypassed.
- Plan-and-launch is bounded; no unbounded polling loop in a Streamlit rerun.
- At most one active attempt per task and per workspace.
- A failed item does not abort the rest of a parallel wave.
- A PR is merged only when policy, checks, review (when configured), and
  mergeability all permit it.
- `Done` means the merge is verified in the target branch.
- Every decision and skip has a machine-readable reason.

## Suggested Claude Code prompt

> Continue in this worktree only. Read this handoff and the current diff.
> Preserve the completed supervisor remediation. Implement Waves A–E in
> dependency order, using only the existing scheduler, execution queue,
> launch, completion, and task-sync state machines. Keep autopilot and
> auto-merge explicit persisted opt-ins. Run focused tests after each wave,
> then Ruff, compileall, full pytest, and git diff --check. Do not touch the
> Git state of the original checkout. Before importing live roadmap tasks,
> run the canonical task-import dry-run and report the exact preview.
