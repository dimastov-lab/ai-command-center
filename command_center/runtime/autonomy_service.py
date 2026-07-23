"""Autonomy proposal orchestration — the side-effecting layer over the pure
`autonomy` domain and the `db` persistence layer.

This is the AICC-AUTONOMY-002 analogue of `completion_service`: it drives a
proposal through its lifecycle, writing an append-only audit event for every
move, but it is deliberately *thinner* and *more restrained* than the completion
orchestrator. The load-bearing safety property is:

    This module NEVER executes anything.

`dispatch` does not launch a run, create a task, merge a branch, or touch a
repository. It records that an approved proposal has crossed the execution
boundary and returns the dry-run `ExecutionPlan` describing what the caller must
now do — explicitly, through the existing routes (`ExecutionCenterAPI.start_run`,
which itself demands `confirmed=True`). `confirm_execution` then records the id
of whatever the caller actually launched. The engine's job is to *govern* the
recommendation -> approval -> execution boundary and prove every decision from
stored evidence; performing the action stays with the existing, already-guarded
execution machinery.

Safety invariants enforced here:

* No silent execution — the only state execution may dispatch from is APPROVED,
  and dispatch requires `policy.allow_execution_dispatch` to be explicitly on.
* Explicit policy + approval — a proposal cannot reach APPROVED without either a
  policy that auto-approves its (non-critical) risk, or an explicit human
  approval call. CRITICAL risk is never auto-approved.
* Reproducibility — `assess` re-derives its verdict purely from the stored
  evidence via `autonomy.evaluate_eligibility`, and persists the verdict; the
  audit trail plus the frozen evidence rows reproduce every decision.
* No UI dependency — pure Python + sqlite; imports no Streamlit, no web layer.
"""

from __future__ import annotations

import json
from pathlib import Path

from command_center.models import iso_now
from command_center.runtime import autonomy, db


class ProposalError(Exception):
    """Base class for autonomy-service errors."""


class UnknownProposalError(ProposalError):
    """No proposal with the given id."""


class DispatchNotPermittedError(ProposalError):
    """A dispatch was requested but the resolved policy does not allow execution
    dispatch (`allow_execution_dispatch` is False). The proposal is left
    untouched — nothing crosses the execution boundary."""


class IllegalProposalActionError(ProposalError):
    """An action was requested from a state that does not permit it (e.g.
    approving a terminal proposal, or dispatching one that is not APPROVED)."""


class AutonomyEngine:
    """Stateless facade (holds only a `db_path`, like `ExecutionCenterAPI`)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or db.resolve_db_path()
        db.migrate(self.db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, proposal_id: str) -> dict:
        row = db.get_proposal(self.db_path, proposal_id)
        if row is None:
            raise UnknownProposalError(f"No such proposal: {proposal_id!r}")
        return row

    def _resolve_policy(self, row: dict, policy: autonomy.AutonomyPolicy | None) -> autonomy.AutonomyPolicy:
        """The *effective* policy for an operation: the persisted policy is
        authoritative, and a caller-supplied runtime `policy` may only further
        restrict it (conservative intersection — see `AutonomyPolicy.intersect`).
        A caller can therefore never make an operation more permissive than the
        policy the proposal was stored/assessed under. A missing/invalid stored
        policy resolves to the deny-by-default `AutonomyPolicy()` and so fails
        closed. This is the F1 invariant."""
        stored = autonomy.AutonomyPolicy.from_json(row.get("policy_json"))
        return stored.intersect(policy)

    def _load_evidence(self, proposal_id: str) -> list[autonomy.Evidence]:
        """Reconstruct the `Evidence` set purely from the frozen evidence rows,
        so an assessment is provably a function of what was stored."""
        rows = db.list_proposal_evidence(self.db_path, proposal_id)
        return [
            autonomy.Evidence.from_dict(
                {
                    "kind": r["kind"],
                    "source": r["source"],
                    "summary": r.get("summary") or "",
                    "observed_at": r["observed_at"],
                    "data": r.get("data") or {},
                    "is_blocker": r.get("is_blocker", False),
                }
            )
            for r in rows
        ]

    def _transition(
        self,
        row: dict,
        new_state: str,
        *,
        event_type: str,
        actor: str | None = None,
        reason_code: str | None = None,
        message: str | None = None,
        extra_fields: dict | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """One compare-and-set lifecycle move + its audit event, applied
        atomically (both in one DB transaction — F2). Returns the updated row.
        The structural guard lives in `db._proposal_update`; this raises
        `IllegalProposalActionError` for a rejected transition so callers get a
        domain error rather than a persistence one."""
        from_state = row["state"]
        fields = dict(extra_fields or {})
        if reason_code is not None:
            fields["last_reason_code"] = reason_code
        event = {
            "event_type": event_type,
            "from_state": from_state,
            "to_state": new_state,
            "actor": actor,
            "reason_code": reason_code,
            "message": message,
            "metadata": metadata,
        }
        try:
            return db.transition_proposal_atomic(
                self.db_path,
                row["id"],
                expected_version=row["version"],
                new_state=new_state,
                event=event,
                fields=fields,
            )
        except db.InvalidProposalTransitionError as exc:
            raise IllegalProposalActionError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_proposal(
        self,
        *,
        kind: str,
        project: str,
        title: str,
        rationale: str,
        evidence: list[autonomy.Evidence] | None = None,
        task_id: str | None = None,
        policy: autonomy.AutonomyPolicy | None = None,
    ) -> dict:
        """Create a proposal in DRAFT with its evidence attached.

        Validates `kind` against the known vocabulary and requires a non-empty
        `rationale` (also enforced at the db layer). Evidence is written as
        immutable rows; the proposal's initial `risk_level` is classified from
        that evidence. Nothing is assessed or executed here."""
        if kind not in autonomy.ALL_KINDS:
            raise ValueError(f"Unknown proposal kind: {kind!r}")
        evidence = list(evidence or [])
        risk = autonomy.classify_risk(kind, evidence)
        digest = autonomy.evidence_digest(evidence)
        # One atomic unit (F2): the proposal row, its immutable evidence, its
        # digest, and the CREATED audit event all commit together or not at all.
        return db.create_proposal_atomic(
            self.db_path,
            kind=kind,
            project=project,
            title=title,
            rationale=rationale,
            state=autonomy.ProposalState.DRAFT,
            risk_level=risk,
            task_id=task_id,
            policy_json=policy.to_json() if policy is not None else None,
            requires_human=True,
            evidence_digest=digest,
            evidence=[e.to_dict() for e in evidence],
            created_event={
                "event_type": autonomy.EventType.CREATED,
                "to_state": autonomy.ProposalState.DRAFT,
                "message": rationale,
                "metadata": {"kind": kind, "risk_level": risk, "evidence_count": len(evidence)},
            },
        )

    # ------------------------------------------------------------------
    # Assess — deterministic, reproducible from stored evidence
    # ------------------------------------------------------------------

    def assess(
        self,
        proposal_id: str,
        *,
        policy: autonomy.AutonomyPolicy | None = None,
        now: str | None = None,
    ) -> dict:
        """Run the deterministic eligibility evaluation and route the proposal.

        Moves DRAFT -> PROPOSED -> (ELIGIBLE | BLOCKED), then an ELIGIBLE
        proposal one further hop to APPROVED (only if policy auto-approves its
        non-critical risk) or AWAITING_APPROVAL. Persists the verdict and the
        dry-run plan so the decision is fully reproducible. Idempotent-ish: only
        proposals in DRAFT/PROPOSED are (re-)assessed; a proposal already past
        assessment is returned unchanged."""
        row = self._require(proposal_id)
        state = row["state"]
        if state not in (autonomy.ProposalState.DRAFT, autonomy.ProposalState.PROPOSED):
            return row

        stored_policy = autonomy.AutonomyPolicy.from_json(row.get("policy_json"))
        resolved_policy = stored_policy.intersect(policy)  # effective = persisted ∩ runtime (F1)
        evidence = self._load_evidence(proposal_id)
        assessed_at = now or iso_now()
        verdict = autonomy.evaluate_eligibility(
            kind=row["kind"], evidence=evidence, policy=resolved_policy, now=assessed_at
        )
        plan = autonomy.build_execution_plan(
            kind=row["kind"],
            title=row["title"],
            rationale=row["rationale"],
            parameters={"task_id": row.get("task_id"), "project": row["project"]},
        )
        digest = autonomy.evidence_digest(evidence)

        # The verdict, its ASSESSED event, and every state transition below are
        # applied as ONE atomic unit (F2): a crash commits all or none, and a
        # concurrent assessor on the same version loses with LostUpdateError.
        # The *effective* (intersected) policy is what gets persisted, so a later
        # dispatch is evaluated against the same policy assessment used (F1).
        verdict_fields = {
            "eligibility_json": json.dumps(verdict.to_dict(), ensure_ascii=False, sort_keys=True),
            "plan_json": json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
            "risk_level": verdict.risk,
            "evidence_digest": digest,
            "requires_human": verdict.requires_human,
            "policy_json": resolved_policy.to_json(),
        }
        policy_prints = {
            "persisted_policy_fingerprint": autonomy.policy_fingerprint(stored_policy),
            "runtime_policy_fingerprint": autonomy.policy_fingerprint(policy),
            "effective_policy_fingerprint": autonomy.policy_fingerprint(resolved_policy),
        }
        assessed_event = {
            "from_state": state,
            "to_state": state,
            "reason_code": verdict.primary_reason,
            "message": f"decision={verdict.decision} risk={verdict.risk}",
            "metadata": {**verdict.to_dict(), "assessed_at": assessed_at, **policy_prints},
        }

        transitions: list[dict] = []
        cur = state
        if cur == autonomy.ProposalState.DRAFT:
            transitions.append(
                self._hop(cur, autonomy.ProposalState.PROPOSED,
                          event_type=autonomy.EventType.TRANSITION, reason_code=verdict.primary_reason)
            )
            cur = autonomy.ProposalState.PROPOSED

        if verdict.decision == autonomy.DECISION_BLOCKED:
            transitions.append(
                self._hop(cur, autonomy.ProposalState.BLOCKED,
                          event_type=autonomy.EventType.TRANSITION, reason_code=verdict.primary_reason,
                          message="blocked by eligibility rules")
            )
        else:
            transitions.append(
                self._hop(cur, autonomy.ProposalState.ELIGIBLE,
                          event_type=autonomy.EventType.TRANSITION, reason_code=verdict.primary_reason)
            )
            cur = autonomy.ProposalState.ELIGIBLE
            if verdict.next_state == autonomy.ProposalState.APPROVED:
                transitions.append(
                    self._hop(cur, autonomy.ProposalState.APPROVED,
                              event_type=autonomy.EventType.APPROVAL, actor="policy:auto",
                              reason_code=autonomy.ReasonCode.AUTO_APPROVED, message="auto-approved by policy",
                              extra_fields={"decided_by": "policy:auto",
                                            "decision_reason": "auto-approved by policy"})
                )
            else:
                transitions.append(
                    self._hop(cur, autonomy.ProposalState.AWAITING_APPROVAL,
                              event_type=autonomy.EventType.TRANSITION,
                              reason_code=autonomy.ReasonCode.HUMAN_APPROVAL_REQUIRED,
                              message="awaiting human approval")
                )

        try:
            return db.apply_assessment_atomic(
                self.db_path,
                proposal_id,
                expected_version=row["version"],
                verdict_fields=verdict_fields,
                assessed_event=assessed_event,
                transitions=transitions,
            )
        except db.InvalidProposalTransitionError as exc:
            raise IllegalProposalActionError(str(exc)) from exc

    @staticmethod
    def _hop(
        from_state: str,
        new_state: str,
        *,
        event_type: str,
        actor: str | None = None,
        reason_code: str | None = None,
        message: str | None = None,
        extra_fields: dict | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Build one transition spec for `db.apply_assessment_atomic`."""
        return {
            "new_state": new_state,
            "extra_fields": dict(extra_fields or {}),
            "event": {
                "event_type": event_type,
                "from_state": from_state,
                "to_state": new_state,
                "actor": actor,
                "reason_code": reason_code,
                "message": message,
                "metadata": metadata,
            },
        }

    # ------------------------------------------------------------------
    # Dry-run planning (pure read; never mutates state)
    # ------------------------------------------------------------------

    def plan(self, proposal_id: str) -> dict:
        """Return the dry-run `ExecutionPlan` for a proposal without changing it.
        Uses the persisted plan if assessment has run, else builds one on the
        fly. Purely descriptive — building a plan executes nothing."""
        row = self._require(proposal_id)
        if row.get("plan_json"):
            return json.loads(row["plan_json"])
        return autonomy.build_execution_plan(
            kind=row["kind"],
            title=row["title"],
            rationale=row["rationale"],
            parameters={"task_id": row.get("task_id"), "project": row["project"]},
        ).to_dict()

    # ------------------------------------------------------------------
    # Human decision gates
    # ------------------------------------------------------------------

    def approve(self, proposal_id: str, *, actor: str, reason: str | None = None) -> dict:
        """Explicit human approval. Legal from AWAITING_APPROVAL or ELIGIBLE, and
        from BLOCKED via an explicit override (BLOCKED -> AWAITING_APPROVAL ->
        APPROVED, both hops audited). `actor` must identify the approver and is
        recorded. Approving a terminal or already-approved proposal is an
        `IllegalProposalActionError`."""
        if not actor or not str(actor).strip():
            raise ValueError("approve requires a non-empty actor")
        row = self._require(proposal_id)
        state = row["state"]
        if state == autonomy.ProposalState.APPROVED:
            return row
        if state == autonomy.ProposalState.BLOCKED:
            row = self._transition(
                row,
                autonomy.ProposalState.AWAITING_APPROVAL,
                event_type=autonomy.EventType.OVERRIDE,
                actor=actor,
                reason_code=autonomy.ReasonCode.OVERRIDDEN_BY_HUMAN,
                message=reason or "human override of a blocked proposal",
            )
        if row["state"] not in (
            autonomy.ProposalState.AWAITING_APPROVAL,
            autonomy.ProposalState.ELIGIBLE,
        ):
            raise IllegalProposalActionError(
                f"Cannot approve proposal in state {row['state']!r}"
            )
        return self._transition(
            row,
            autonomy.ProposalState.APPROVED,
            event_type=autonomy.EventType.APPROVAL,
            actor=actor,
            reason_code=autonomy.ReasonCode.APPROVED_BY_HUMAN,
            message=reason or "approved",
            extra_fields={
                "decided_by": actor,
                "decision_reason": reason or "approved",
                "requires_human": False,
            },
        )

    def reject(self, proposal_id: str, *, actor: str, reason: str) -> dict:
        """Explicit rejection (terminal). Legal from any non-terminal state that
        can reach REJECTED (AWAITING_APPROVAL, BLOCKED)."""
        if not actor or not str(actor).strip():
            raise ValueError("reject requires a non-empty actor")
        if not reason or not str(reason).strip():
            raise ValueError("reject requires a reason")
        row = self._require(proposal_id)
        return self._transition(
            row,
            autonomy.ProposalState.REJECTED,
            event_type=autonomy.EventType.REJECTION,
            actor=actor,
            reason_code=autonomy.ReasonCode.REJECTED_BY_HUMAN,
            message=reason,
            extra_fields={"decided_by": actor, "decision_reason": reason},
        )

    def withdraw(self, proposal_id: str, *, actor: str, reason: str | None = None) -> dict:
        """Withdraw a proposal (terminal). Legal from any non-terminal state."""
        if not actor or not str(actor).strip():
            raise ValueError("withdraw requires a non-empty actor")
        row = self._require(proposal_id)
        return self._transition(
            row,
            autonomy.ProposalState.WITHDRAWN,
            event_type=autonomy.EventType.WITHDRAWAL,
            actor=actor,
            reason_code=autonomy.ReasonCode.WITHDRAWN,
            message=reason or "withdrawn",
            extra_fields={"decided_by": actor, "decision_reason": reason or "withdrawn"},
        )

    # ------------------------------------------------------------------
    # Execution boundary — records the crossing; NEVER performs the action
    # ------------------------------------------------------------------

    def dispatch(
        self,
        proposal_id: str,
        *,
        actor: str,
        policy: autonomy.AutonomyPolicy | None = None,
    ) -> dict:
        """Cross the execution boundary for an APPROVED proposal.

        This does NOT launch, create, or merge anything. It verifies the policy
        permits execution dispatch, moves APPROVED -> DISPATCHED, and returns
        ``{"proposal": row, "plan": <ExecutionPlan dict>}``. The caller is then
        responsible for performing exactly the planned action through the
        existing, confirmation-gated execution routes, and calling
        `confirm_execution` with the resulting id.

        Raises `IllegalProposalActionError` if the proposal is not APPROVED, and
        `DispatchNotPermittedError` (leaving the proposal untouched) if the
        policy does not allow execution dispatch."""
        if not actor or not str(actor).strip():
            raise ValueError("dispatch requires a non-empty actor")
        row = self._require(proposal_id)
        if row["state"] != autonomy.ProposalState.APPROVED:
            raise IllegalProposalActionError(
                f"Only an APPROVED proposal may be dispatched; state is {row['state']!r}"
            )
        # Effective policy = persisted ∩ runtime (F1): a caller-supplied policy
        # can only further restrict, never re-permit, dispatch. The persisted
        # policy the proposal was approved under stays authoritative.
        stored_policy = autonomy.AutonomyPolicy.from_json(row.get("policy_json"))
        resolved_policy = stored_policy.intersect(policy)
        # Non-sensitive policy fingerprints for the audit trail (F1) — a digest,
        # never the raw policy JSON.
        policy_prints = {
            "persisted_policy_fingerprint": autonomy.policy_fingerprint(stored_policy),
            "runtime_policy_fingerprint": autonomy.policy_fingerprint(policy),
            "effective_policy_fingerprint": autonomy.policy_fingerprint(resolved_policy),
        }
        if not resolved_policy.allow_execution_dispatch:
            # The proposal stays APPROVED; nothing crosses the boundary. Record
            # the refusal (with the fingerprints and result) in the audit trail
            # without a state change.
            db.append_proposal_event(
                self.db_path,
                proposal_id,
                autonomy.EventType.DISPATCH,
                from_state=row["state"],
                to_state=row["state"],
                actor=actor,
                reason_code=autonomy.ReasonCode.DISPATCH_DISABLED,
                message="dispatch refused: effective policy.allow_execution_dispatch is False",
                metadata={**policy_prints, "dispatch_allowed": False},
            )
            raise DispatchNotPermittedError(
                "Effective policy does not allow execution dispatch "
                "(persisted ∩ runtime allow_execution_dispatch is False)"
            )
        plan = self.plan(proposal_id)
        updated = self._transition(
            row,
            autonomy.ProposalState.DISPATCHED,
            event_type=autonomy.EventType.DISPATCH,
            actor=actor,
            reason_code=autonomy.ReasonCode.DISPATCHED,
            message="approved plan handed to caller for explicit execution",
            metadata={"plan": plan, **policy_prints, "dispatch_allowed": True},
        )
        return {"proposal": updated, "plan": plan}

    def confirm_execution(
        self,
        proposal_id: str,
        *,
        actor: str,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Record that the caller performed the dispatched action. Moves
        DISPATCHED -> EXECUTED and links the resulting `run_id`/`task_id` so the
        proposal points at the real work it authorised."""
        if not actor or not str(actor).strip():
            raise ValueError("confirm_execution requires a non-empty actor")
        row = self._require(proposal_id)
        extra: dict = {}
        if run_id is not None:
            extra["dispatched_run_id"] = run_id
        if task_id is not None:
            extra["dispatched_task_id"] = task_id
        return self._transition(
            row,
            autonomy.ProposalState.EXECUTED,
            event_type=autonomy.EventType.EXECUTION,
            actor=actor,
            reason_code=autonomy.ReasonCode.EXECUTION_CONFIRMED,
            message="execution confirmed and linked",
            extra_fields=extra,
            metadata={"run_id": run_id, "task_id": task_id},
        )

    def fail_dispatch(self, proposal_id: str, *, actor: str, reason: str) -> dict:
        """Record that a dispatched action could not be carried out. Moves
        DISPATCHED -> BLOCKED for a human to inspect (never silently back to
        APPROVED)."""
        if not actor or not str(actor).strip():
            raise ValueError("fail_dispatch requires a non-empty actor")
        if not reason or not str(reason).strip():
            raise ValueError("fail_dispatch requires a reason")
        row = self._require(proposal_id)
        return self._transition(
            row,
            autonomy.ProposalState.BLOCKED,
            event_type=autonomy.EventType.DISPATCH,
            actor=actor,
            reason_code=autonomy.ReasonCode.DISPATCH_FAILED,
            message=reason,
        )

    # ------------------------------------------------------------------
    # Read projections
    # ------------------------------------------------------------------

    def get(self, proposal_id: str) -> dict | None:
        return db.get_proposal(self.db_path, proposal_id)

    def list(
        self,
        *,
        project: str | None = None,
        states=None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        return db.list_proposals(
            self.db_path, project=project, states=states, kind=kind, limit=limit
        )

    def evidence(self, proposal_id: str) -> list[dict]:
        return db.list_proposal_evidence(self.db_path, proposal_id)

    def events(self, proposal_id: str, *, limit: int = 500) -> list[dict]:
        return db.list_proposal_events(self.db_path, proposal_id, limit=limit)


def observe(
    kind: str,
    source: str,
    summary: str,
    *,
    data: dict | None = None,
    is_blocker: bool = False,
    observed_at: str | None = None,
) -> autonomy.Evidence:
    """Convenience constructor for a piece of real, attributable evidence.
    `source` is mandatory (enforced by `Evidence`) so every observation records
    where it came from — the engine never fabricates evidence."""
    return autonomy.Evidence(
        kind=kind,
        source=source,
        summary=summary,
        observed_at=observed_at or iso_now(),
        data=dict(data or {}),
        is_blocker=is_blocker,
    )
