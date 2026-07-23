# ADR 0005 — Autonomy Proposal Foundation (AICC-AUTONOMY-002)

Status: **Accepted, implemented.**

## Context

ADR 0004 gave the system a deterministic, restart-safe **post-execution**
pipeline: once a run finishes, the completion state machine governs validation →
PR → merge, conservatively (never auto-merging without explicit opt-in). But
that pipeline only ever engages *after a human has already decided to run
something*. The decision **upstream** of execution — *should the engine create
this task, run this task, change this priority, merge this branch at all?* —
had no home.

The only thing resembling autonomy on the pre-execution side was
`recommend.Recommendation`: an in-memory score with a list of `reasons`. It is a
read-only ranking of existing tasks; it has no persistence, no lifecycle, no
risk classification, no evidence store, no approval state, and no audit trail.
Nothing recorded *why* the engine wanted to act, *whether it was allowed to*, or
*who approved it*. There was no safe place to let the system propose work
without that proposal silently becoming execution.

For an autonomy engine to grow safely toward its eventual scope (repository
analysis, gap detection, roadmap and task generation, prioritisation, dependency
and execution planning, remediation and merge recommendations, continuous
self-improvement), the **first** thing it needs is not more capability — it is a
governed, explainable boundary between **recommendation, approval, and
execution**, with risky actions blocked by default.

## Decision

Introduce a **deterministic, persisted, evidence-backed proposal lifecycle** —
the pre-execution decision layer — that makes the recommendation → approval →
execution boundary explicit and auditable. It mirrors the completion pipeline's
three-layer split (pure domain / persistence / orchestration) and reuses its
transition-guard, compare-and-set, and audit-event idioms, but it is
deliberately *more restrained*: **the autonomy layer never executes anything.**

### 1. A proposal is not a recommendation and not an execution

| Concept | Owner | Meaning |
|---|---|---|
| `Recommendation` | `recommend.py` | Ephemeral "what to work on next" score. Unchanged. |
| **`AutonomyProposal`** | `runtime.autonomy` + `db` | Persisted, risk-classified, evidence-backed suggestion moving through a governed lifecycle. |
| `run` / `completion` | `runtime.db` / `runtime.completion` | Execution and post-execution. Unchanged. |

A proposal records *why it exists* (`rationale`, required and non-empty at the
persistence boundary), *what it is based on* (immutable evidence rows), *how
dangerous it is* (`risk_level`), *whether it is allowed* (the stored eligibility
verdict), *who decided* (`decided_by`), and *what it eventually authorised*
(`dispatched_run_id`/`dispatched_task_id`).

### 2. Proposal state machine

```
DRAFT ─▶ PROPOSED ─▶ ELIGIBLE ─▶ AWAITING_APPROVAL ─▶ APPROVED ─▶ DISPATCHED ─▶ EXECUTED
                 │            │            ▲                │            │
                 └▶ BLOCKED ──┘   (auto)   │   APPROVED ◀───┘            └▶ BLOCKED
                     │  └──── override ─────┘
                     └▶ REJECTED
   (WITHDRAWN reachable from every non-terminal state except DISPATCHED)
```

Terminal states are `EXECUTED`, `REJECTED`, `WITHDRAWN`. The allowed edges are
explicit *data* (`autonomy.PROPOSAL_TRANSITIONS`), and
`is_valid_proposal_transition` is consulted by `db.update_proposal` before any
compare-and-set write — a backward jump or a move out of a terminal state is
refused at the persistence layer, independent of orchestration control flow
(exactly as `update_completion` guards `completion_state`). Same-state updates
(evidence enrichment / metadata) are always allowed.

`DISPATCHED` deliberately cannot be withdrawn: once the execution boundary is
crossed it is confirmed (`EXECUTED`) or failed back (`BLOCKED`), never silently
abandoned.

### 3. Evidence model — real, attributable, reproducible

An `Evidence` item carries a `kind`, a **mandatory `source`** (an observation
with no source is a claim, not evidence, and is rejected in `__post_init__`), a
`summary`, an `observed_at` timestamp, structured `data`, and an `is_blocker`
flag. Evidence rows are **append-only and never updated** — the observations a
decision rests on stay frozen, so the decision remains reproducible from them. A
content-addressed `evidence_digest` (order-independent SHA-256) is stored so a
reviewer can confirm the evidence has not silently changed.

The domain module never *invents* evidence; it only classifies and evaluates
what a caller collected. This is the "no fabricated evidence" rule made
structural.

### 4. Deterministic eligibility + risk classification

`classify_risk(kind, evidence)` is a pure function: a per-kind floor
(TASK_CREATION/PRIORITY_CHANGE/DEPENDENCY_LINK → LOW, TASK_EXECUTION → HIGH,
MERGE → CRITICAL, unknown → HIGH) escalated — never de-escalated — by blocker /
`risk_signal` / `sensitive` markers in the evidence.

`evaluate_eligibility(kind, evidence, policy, now)` is the single, pure,
reproducible source of truth for autonomy safety. Its checks run
hardest-block-first so a permissive later branch can never overtake a hard
denial:

1. policy disabled → `BLOCKED` (`POLICY_DISABLED`);
2. kind not in `policy.allowed_kinds` → `BLOCKED` (`KIND_NOT_ALLOWED`);
3. no evidence → `BLOCKED` (`EVIDENCE_MISSING`);
4. any stale evidence → `BLOCKED` (`EVIDENCE_STALE`);
5. any blocker observation → `BLOCKED` (`EVIDENCE_BLOCKER`);
6. CRITICAL risk → `AWAITING_APPROVAL` (human), never auto;
7. risk ≤ `auto_approve_max_risk` and policy enabled → `APPROVED` (auto);
8. otherwise → `AWAITING_APPROVAL` (human).

Every path that is not an explicit, in-policy auto-approval requires a human —
risky actions default to blocked.

### 5. Autonomy policy — conservative by construction

`AutonomyPolicy()` with no arguments is **fully closed**: `enabled=False`,
`allowed_kinds=∅`, `auto_approve_max_risk=NONE`, `allow_execution_dispatch=False`.
A brand-new install proposes nothing and auto-executes nothing. `CRITICAL` risk
is never auto-approvable even if a policy names it (it is clamped to `HIGH`, and
`may_auto_approve` refuses CRITICAL outright).

### 6. The execution boundary — recorded, never performed

This is the load-bearing safety property. `autonomy_service.dispatch`:

* refuses unless the proposal is `APPROVED`;
* refuses unless `policy.allow_execution_dispatch` is explicitly on — a refusal
  leaves the proposal untouched and is itself written to the audit trail;
* on success moves `APPROVED → DISPATCHED` and **returns the dry-run
  `ExecutionPlan`** — it does not launch a run, create a task, or merge.

The caller then performs exactly the planned action through the existing,
already-guarded route (`ExecutionCenterAPI.start_run`, which itself demands
`confirmed=True` and honours the agent `--disallowedTools` git sandbox), and
reports the resulting id back via `confirm_execution` (`DISPATCHED → EXECUTED`).
The autonomy layer *governs and records* the boundary; the existing execution
machinery *is* the boundary. Agents still cannot commit/push/merge; only the
completion orchestrator can, unchanged.

### 7. Persistence — schema 6, restart-safe, audited

Three tables added via migration 6 (`SCHEMA_VERSION` 5 → 6), following the
completion-row idioms exactly:

* `proposal` — one mutable current-state row, `version`-guarded compare-and-set,
  transition-guarded; identity columns write-once, mutable columns on an
  allowlist (`_UPDATABLE_PROPOSAL_FIELDS`).
* `proposal_evidence` — append-only, immutable, per-proposal `seq`.
* `proposal_event` — append-only audit trail (from/to state, actor, reason code,
  message, metadata), per-proposal `seq`.

Every lifecycle move writes an event, so the full decision history — including
*why* (the `CREATED` event carries the rationale) and *who* — is reconstructable,
and the stored eligibility verdict plus frozen evidence reproduce every decision.

### 8. No UI dependency

`autonomy.py` and `autonomy_service.py` import no Streamlit, no web layer, no
subprocess, no network — pure Python over `models` and `db`. The application
surface is a set of thin passthroughs on `ExecutionCenterAPI`
(`create_proposal`, `assess_proposal`, `plan_proposal`, `approve_proposal`,
`reject_proposal`, `withdraw_proposal`, `dispatch_proposal`,
`confirm_proposal_execution`, and the read projections), so a UI can be layered
on later without the runtime depending on it.

## Consequences

**Positive**: the system can now *propose* work — including risky work — without
any risk that a proposal silently becomes execution. Every proposal explains
itself, every decision is reproducible from stored evidence, and the
recommendation/approval/execution distinction is explicit in the data model, not
a convention. The foundation is extensible toward the full autonomy roadmap
(gap detection, roadmap/task generation, planning) by adding proposal *kinds*
and *evidence sources*, not by loosening the safety envelope.

**Trade-off accepted**: this increment adds no new *evidence collectors* — it
does not itself analyse repositories or detect gaps. Callers supply evidence.
That is deliberate: the safety and lifecycle scaffolding must exist and be
trusted before any automated evidence source is wired in, so nothing can act on
un-governed input.

**Trade-off accepted**: dispatch is a two-step handshake (dispatch → caller
executes → confirm) rather than a single call. This is the point — it keeps the
autonomy layer out of the execution path entirely.

### Addendum (MINOR-REMEDIATION-001) — policy authority & atomicity

Two Founder-Gate findings were closed without changing the architecture above:

* **Persisted policy is authoritative (F1).** A caller-supplied runtime policy
  can now only *further restrict* the persisted policy, never widen it. The
  effective policy for `assess`/`dispatch` is the conservative *intersection*
  (`AutonomyPolicy.intersect`) of persisted ∩ runtime: `enabled` and
  `allow_execution_dispatch` combine by AND, `allowed_kinds` by set
  intersection, `auto_approve_max_risk` by the lower ceiling, evidence window by
  the stricter bound. A missing/invalid stored policy resolves to the
  deny-by-default policy (fails closed). `assess` persists the *effective* policy
  it evaluated, so a later `dispatch` is judged against the same policy the
  approval was made under. Dispatch audit events carry non-sensitive policy
  *fingerprints* (persisted / runtime / effective) and the allowed/denied result
  — never raw policy JSON.
* **Atomic creation & assessment (F2).** Proposal creation (row + evidence +
  digest + CREATED event) and assessment (verdict + ASSESSED event + every state
  transition) each commit as a single database transaction via
  `db.create_proposal_atomic` / `db.apply_assessment_atomic` (and every lifecycle
  move via `db.transition_proposal_atomic`). A crash commits all-or-nothing; a
  stale/concurrent writer loses with `LostUpdateError` and writes nothing; there
  is exactly one CREATED and one ASSESSED event per committed operation, with a
  monotonic audit sequence. Future-dated evidence is treated as stale.

## Known limitations

* No automated evidence collectors yet (repository analysis, gap detection); the
  engine governs decisions but does not originate observations.
* No scheduled/background driver — proposals are created and advanced on demand,
  not by a poller. (Autonomy stays off until explicitly driven.)
* No per-project persisted `AutonomyPolicy` resolution layer yet; a policy is
  passed in or stored per-proposal. Project/task-level policy resolution
  (as `CompletionPolicy.resolve` does) is a natural follow-up.
* Evidence staleness uses the project-wide naive-local timestamp convention
  (`models.iso_now`); cross-machine clock skew is out of scope, as elsewhere.

## Verification

`ruff check .`, `python -m compileall`, and `pytest` all clean. New coverage:
`tests/test_autonomy_domain.py` (policy defaults, risk classification, the state
machine, evidence model, and every eligibility branch including denials and
malformed input), `tests/test_autonomy_db.py` (migration 6, CRUD, compare-and-set,
the transition guard preceding the version check, the field allowlist, immutable
evidence, ordered audit events), `tests/test_autonomy_service.py` (the full
lifecycle: blocked path, human-gate path, auto-approval, dispatch refusal,
dispatch + confirm, reject/withdraw/override, critical-never-auto,
reproducibility, and the complete audit trail), and `tests/test_autonomy_api.py`
(the `ExecutionCenterAPI` facade). `scripts/demo_autonomy_proposals.py` runs the
four scenarios end to end against a throwaway store without launching a run or
touching a repository.
