# ADR 0005 — Autonomy Proposal Foundation (AICC-AUTONOMY-002)

Status: **Accepted, implemented. Experimental foundation** — persisted, evidence-backed
proposal lifecycle only. There is no Streamlit UI, automated evidence collector,
project policy resolver, background driver, or executor: `dispatch` records the
boundary crossing and returns a dry-run plan the caller must run explicitly. Do not
extend this layer without first deciding whether to complete it or retire it.

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
persistence boundary), *what it is based on* (evidence rows frozen at
assessment), *how dangerous it is* (`risk_level`), *whether it is allowed* (the
stored eligibility verdict), *who decided* (`decided_by`), the canonical action
arguments it authorises (`parameters_json` plus an `action_digest` in the plan),
and the matching result eventually confirmed
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
explicit *data* (`autonomy.PROPOSAL_TRANSITIONS`). Persistence checks the
expected row version first, then applies a lifecycle-scoped field allowlist and
`is_valid_proposal_transition`. A stale writer therefore always loses as a
stale writer, while a current writer still cannot jump backward, leave a
terminal state, or rewrite authority-bearing fields after assessment. A
same-state compare-and-set does not grant arbitrary mutation authority.

`DISPATCHED` deliberately cannot be withdrawn: once the execution boundary is
crossed it is confirmed (`EXECUTED`) or failed back (`BLOCKED`), never silently
abandoned.

### 3. Evidence model — real, attributable, reproducible

An `Evidence` item carries a `kind`, a **mandatory `source`** (an observation
with no source is a claim, not evidence, and is rejected in `__post_init__`), a
`summary`, an `observed_at` timestamp, structured `data`, and an `is_blocker`
flag. Evidence rows are append-only while a proposal is being prepared and are
fully frozen once assessment begins. The observations a decision rests on
therefore remain reproducible. A content-addressed `evidence_digest`
(order-independent SHA-256) is stored so a reviewer can confirm the evidence
has not silently changed.

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

Hard denials become `BLOCKED`; eligible actions outside the explicit
auto-approval ceiling enter `AWAITING_APPROVAL` and require a human.

### 5. Autonomy policy — conservative by construction

`AutonomyPolicy()` with no arguments is **fully closed**: `enabled=False`,
`allowed_kinds=∅`, `auto_approve_max_risk=NONE`, `allow_execution_dispatch=False`.
A brand-new install proposes nothing and auto-executes nothing. `CRITICAL` risk
is never auto-approvable even if a policy names it (it is clamped to `HIGH`, and
`may_auto_approve` refuses CRITICAL outright).

Policy parsing is strict at this authority boundary. Boolean-looking strings,
unknown proposal kinds or risk values, unexpected keys, and non-integer
staleness windows do not get coerced; any malformed policy resolves to the
fully closed default.

### 6. The execution boundary — recorded, never performed

This is the load-bearing safety property. `autonomy_service.dispatch`:

* refuses unless the proposal is `APPROVED`;
* rechecks that the effective policy is enabled, permits the proposal kind, and
  explicitly permits dispatch;
* rebuilds the plan from frozen `parameters_json` and refuses an incomplete
  payload or a mismatch with the persisted action digest;
* verifies the frozen evidence digest and re-runs eligibility at the current
  time, so stale or newly inconsistent evidence cannot cross the boundary;
* records every refusal while leaving the proposal `APPROVED`;
* on success moves `APPROVED → DISPATCHED` and **returns the dry-run
  `ExecutionPlan`** — it does not launch a run, create a task, or merge.

The caller then performs the returned action through the existing,
already-guarded route (`ExecutionCenterAPI.start_run` itself demands
`confirmed=True` and honours the agent `--disallowedTools` git sandbox).
`confirm_execution` accepts only a real persisted task or run whose identity
and core action fields match the authorised payload; an empty, foreign, or
mismatched result is audited and refused. The autonomy layer *governs and
records* the boundary; the existing execution machinery *is* the boundary.
Agents still cannot commit/push/merge; only the completion orchestrator can,
unchanged.

### 7. Persistence — schema 7, restart-safe, audited

Three tables added via migration 6 (`SCHEMA_VERSION` 5 → 6), following the
completion-row idioms:

* `proposal` — one mutable current-state row, `version`-guarded compare-and-set,
  transition-guarded; identity columns write-once and mutable columns governed
  by a lifecycle-scoped allowlist.
* `proposal_evidence` — append-only before assessment and frozen afterward,
  per-proposal `seq`.
* `proposal_event` — append-only audit trail (from/to state, actor, reason code,
  message, metadata), per-proposal `seq`.

Migration 7 (`SCHEMA_VERSION` 6 → 7) adds
`proposal.parameters_json TEXT NOT NULL DEFAULT '{}'`. Writes validate that it
is a JSON object and store canonical key ordering. Existing schema-6 rows
migrate without losing proposal, evidence, event, completion, run, or task
data.

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

### Addendum (INTEGRATION-REMEDIATION-002) — frozen action authority

The integration audit closed the remaining execution-boundary gaps:

* **Strict policy parsing.** Malformed values close the entire policy; Python
  truthiness and integer coercions cannot turn strings into authority.
* **Lifecycle-scoped authority.** Expected-version CAS is checked first.
  Policy, action parameters, evidence digest, verdict, risk, and plan can change
  only before assessment. Evidence append is rejected after assessment.
* **Canonical action binding.** Every kind has a bounded parameter vocabulary
  and required fields. Plans expose completeness and a SHA-256 action digest.
  Unknown arguments are rejected, and incomplete plans cannot dispatch.
* **Dispatch-time validation.** Policy, kind, dispatch permission, action
  digest, evidence digest, blockers, and current staleness are rechecked at the
  boundary.
* **Result binding.** TASK_CREATION and TASK_EXECUTION confirmations must link
  an existing task/run matching the approved core payload. Foreign or empty
  confirmations leave the proposal `DISPATCHED` and append a refusal event.

## Known limitations

* No automated evidence collectors yet (repository analysis, gap detection); the
  engine governs decisions but does not originate observations.
* No scheduled/background driver — proposals are created and advanced on demand,
  not by a poller. (Autonomy stays off until explicitly driven.)
* No per-project persisted `AutonomyPolicy` resolution layer yet; a policy is
  passed in or stored per-proposal. Project/task-level policy resolution
  (as `CompletionPolicy.resolve` does) is a natural follow-up.
* PRIORITY_CHANGE, DEPENDENCY_LINK, and MERGE remain advisory and cannot
  dispatch until durable execution-and-result adapters exist. Only
  TASK_CREATION and TASK_EXECUTION currently produce dispatchable plans.
* There is no autonomy UI, evidence collector, background driver, or implicit
  workspace isolation. Dispatch returns a plan; it does not execute that plan.
* Evidence staleness uses the project-wide naive-local timestamp convention
  (`models.iso_now`); cross-machine clock skew is out of scope, as elsewhere.

## Verification

`ruff check .`, `python -m compileall`, and `pytest` are the validation gates.
Coverage includes:
`tests/test_autonomy_domain.py` (policy defaults, risk classification, the state
machine, evidence model, and every eligibility branch including denials and
malformed input), `tests/test_autonomy_db.py` (migrations 6 and 7, data
preservation, CRUD, CAS-first behavior, lifecycle field authority, frozen
evidence, and ordered audit events), `tests/test_autonomy_service.py` (the full
lifecycle: blocked path, human-gate path, auto-approval, dispatch refusal,
dispatch-time evidence checks, bound confirm, reject/withdraw/override,
critical-never-auto, reproducibility, and the complete audit trail), and
`tests/test_autonomy_api.py`
(the `ExecutionCenterAPI` facade). `scripts/demo_autonomy_proposals.py` runs the
four scenarios end to end against a throwaway store without launching a run or
touching a repository.
