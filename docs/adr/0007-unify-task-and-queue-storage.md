# ADR 0007 — Unify task and execution-queue storage

Status: **Accepted; step 1 (dual-write) implemented.** The schema-migration and dual-write phase
is live: `runtime.db` has a `queue_entry` table (added at schema version 10), and `execution_queue`
mirrors every write into it through `_mirror_to_runtime_db`, with `backfill_mirror` and
`queue_divergence` providing the step-2 backfill and read-time verification. Reads still come from
`execution_queue.json`, which remains authoritative; the later phases (flip the read path, then
stop writing JSON) are not implemented. Because the change moves live operator data (122 tasks,
24 queue entries at the time of writing), each remaining phase stays independently gated and
reversible, and a half-finished attempt would be worse than the problem it fixes.

## Context

Application state lives in four stores, each with its own lock and its own
consistency rules:

| Store | Owner | Lock |
|---|---|---|
| `data/tasks.json` | `tasks_repository` | `tasks.lock` |
| `data/execution_queue.json` | `execution_queue` | `execution_queue.lock` |
| `data/runtime.db` (SQLite) | `runtime.db` | SQLite transactions + CAS |
| `data/pipeline_settings.json` | `pipeline_settings` | `pipeline_settings.lock` |

Each store is individually sound. The problem is the **invariants that span
them**, which no single lock protects and which `task_pipeline.tick` currently
maintains by careful ordering alone:

- a queue entry's `task_id` must reference a task that exists (`tasks.json`);
- a queue entry's `state` must not say `ready` for a task whose dependencies
  regressed (recomputed defensively on every tick — see `adapt_ready_entries`,
  which deliberately re-derives `dependencies_met` rather than trusting the
  persisted label);
- a task's `status == "Done"` must agree with its completion row in
  `runtime.db`;
- at most one active attempt per task, enforced independently in three places
  because no store can enforce it alone.

That last point is the tell. The invariant is real, so it gets enforced three
times — by the pipeline lock, by the planner, and by the launcher. Triple
enforcement is defensible for a safety property, but it exists here partly
*because* the state is split, and it is a standing cost on every change.

Nothing is broken today. This ADR is written while the system is green
(1855 tests passing) precisely so the migration is not attempted under
pressure.

## Decision

Move **execution-queue entries into `runtime.db`** as a new table. Leave
`tasks.json` and `pipeline_settings.json` where they are.

This is deliberately *not* "consolidate all four stores".

### Why the queue moves

The queue is execution state. ADR 0003 already assigns execution state to
`runtime.db` as its sole source of truth; the queue was put in a JSON file
because it was framed as a planning-level list, and that framing has not held
up — the queue now carries `run_id`, `launched_at`, and a lifecycle that only
means something in terms of runs. Its cross-store invariants are all against
`runtime.db` rows, so co-locating them turns four hand-maintained invariants
into foreign keys and one transaction.

### Why tasks do not move

`tasks.json` is the Kanban planning surface. It is edited by humans, diffed,
inspected, and hand-repaired; it survived every incident in this project's
history precisely because it is a readable file. Moving it into SQLite would
buy one referential integrity constraint and cost the ability to open the file
and see what is wrong. That trade is not worth it, and "one store to rule them
all" is not a goal in itself.

The task↔queue invariant is instead handled by keeping the queue's reference
to a task **advisory**: a queue entry whose task has vanished resolves to
`cancelled` with a reason, which is what `evaluate_readiness` already does
today. This ADR does not introduce a foreign key across the boundary; it
removes the *queue's own* split from the run state it describes.

### Why settings do not move

`pipeline_settings.json` answers "what is this machine permitted to do". It is
operator configuration, not execution state, and it must be readable and
editable without the application running — including to turn automation *off*
when something has gone wrong. A settings file that requires a working
application to disable automation is a hazard, not a simplification.

## Migration

The migration must be reversible at every step and must never be the only copy
of the data.

1. **Add the table, write to both.** A new `queue_entry` table in a schema
   migration. `execution_queue` writes to both stores and reads from JSON.
   Rollback: drop the table; JSON is authoritative and untouched.
2. **Backfill and verify.** A one-shot import of existing JSON entries, then a
   read-time comparison that logs any divergence between the two stores without
   changing behaviour. Runs for at least one full working session.
   Rollback: unchanged — JSON is still authoritative.
3. **Flip the read path.** Reads come from SQLite; writes still go to both.
   Rollback: flip the read path back, one constant.
4. **Stop writing JSON.** Only after a session with no divergence logged. The
   JSON file is left in place, not deleted, as a dated snapshot.

A pre-migration copy of `data/` is taken outside the repository before step 1
(`_local-backups/`, as already used for the task-store import).

**Rollback trigger:** any divergence in step 2, any lost update, or any test
regression. Rollback is a code change, never a data restore — that is the
point of the dual-write phases.

## Consequences

- `execution_queue`'s public functions keep their signatures. Callers
  (`task_pipeline`, `queue_panel`, `recommendations_panel`, `waves_panel`) do
  not change; this is a storage change, not an API change.
- The queue's read-modify-write cycle becomes a SQLite transaction, and
  `execution_queue.lock` is removed — one fewer advisory lock, and one fewer
  ordering rule between locks.
- Queue-to-run consistency becomes checkable in one query instead of by
  ordering steps inside `tick`.
- `task_pipeline.tick` keeps its ordering. This ADR does not attempt to
  simplify the tick; that is a separate concern (the tick is also too long, but
  conflating the two changes would make both unreviewable).
- Schema version increments. The migration is idempotent and additive, like
  every migration before it.

## Non-goals

- Consolidating `tasks.json` into SQLite (see above).
- Consolidating `pipeline_settings.json` (see above).
- Restructuring `task_pipeline.tick`.
- Introducing a general-purpose ORM or query layer. `runtime.db` is hand-written
  SQL with explicit migrations, and that has been an asset.

## Divergence handling (decided)

When the two stores disagree during the dual-write phases, the JSON store stays
authoritative, the divergence is logged, and **the count is surfaced in the
autopilot panel** rather than only in a log file.

Failing loudly was the alternative and was rejected: a storage inconsistency
would then take down the very tool the operator uses to inspect and repair
state — the failure mode would be "the dashboard is gone" at exactly the moment
the dashboard is needed. Logging alone was also rejected, for the opposite
reason: a divergence written only to a log is a divergence nobody reads, and
step 4 (stop writing JSON) is gated on "a session with no divergence", which is
a claim the operator must be able to *see* rather than take on faith.

Concretely:

- `execution_queue` compares the two reads on every read during phases 2–3 and
  increments a counter when they differ, recording the entry id and which
  fields disagreed.
- The count is exposed the way the pipeline already exposes its own health: as
  a field on `PipelineTickResult`, rendered by `autopilot_panel` next to the
  existing tick summary. Zero divergences render nothing — a permanent "0" is
  noise that trains people to ignore the row.
- A non-zero count is rendered as a warning, not an error: the system is still
  correct (JSON is authoritative), but step 4 must not proceed.

This mirrors how the pipeline already treats a transient completion-advance
failure: recorded, surfaced, and non-fatal, because degrading a tick is better
than aborting one.
