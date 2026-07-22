"""Minimal Execution Center backend API.

Exactly the service surface Sprint 1 calls for: list sessions, list runs,
inspect run status, read incremental events, request cancellation, reconcile
stale runs (plus launching a run, since nothing else in this codebase does
that yet for v2). This is a plain Python facade over `supervisor.Supervisor`
and `db.py` — no HTTP layer, no redesigned Streamlit UI.
`scripts/execution_center_debug.py` is the minimal debug view used to
validate this module end to end, per the Sprint 1 brief.

`start_run` is the **only** application-facing launch route, and it is
deliberately *not* a thin passthrough to a raw prompt: it takes a user
`instruction` plus optional `candidate_content`/`confirmed_items`, always
calls `context_service.assemble_context` itself, and only then builds the
final provider prompt (`context_service.build_prompt`). A caller cannot
bypass the BANK/LEGAL sensitive-content boundary by pre-building a prompt
and handing it in — there is no parameter here that accepts one. The
low-level `supervisor.Supervisor.start_raw` (which does accept a raw,
already-final prompt) is intentionally not exposed as `ExecutionCenterAPI`
launch method; it exists for `start_run` itself to call and for tests that
need to exercise process-lifecycle mechanics directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from command_center.runtime import autonomy, autonomy_service, context_service, db, supervisor

DEFAULT_TIMEOUT_SECONDS = 900


class ExecutionCenterAPI:
    def __init__(self, db_path: Path | None = None, sup: supervisor.Supervisor | None = None) -> None:
        self.supervisor = sup or supervisor.Supervisor(db_path)
        self.db_path = self.supervisor.db_path

    # ------------------------------------------------------------------
    # Launch (requires explicit confirmation; enforced in Supervisor.start_raw)
    # ------------------------------------------------------------------

    def start_run(
        self,
        *,
        project: str,
        repository_path: str,
        task_type: str,
        instruction: str,
        confirmed: bool,
        candidate_content: dict[str, str] | None = None,
        confirmed_items: list[str] | None = None,
        metadata: dict | None = None,
        task_id: str | None = None,
        title: str | None = None,
        session_id: str | None = None,
        is_resume: bool = False,
        model: str | None = None,
        timeout_seconds: int | None = DEFAULT_TIMEOUT_SECONDS,
        expected_branch: str | None = None,
        launch_source: str | None = None,
        prompt_version: int | None = None,
        repository_already_validated: bool = False,
    ) -> dict:
        """Launch a run. The final prompt sent to `claude` is always built
        internally from `instruction` plus whatever `context_service.
        assemble_context` decides to include — for a sensitive project
        (BANK/LEGAL, decided by `project_config`, never overridable by the
        caller), any `candidate_content` item is included only if its key is
        also in `confirmed_items`. The resulting outbound-content manifest
        (which content keys actually left the machine, and which were
        excluded) is persisted as a `context_manifest` run event — auditable
        via `get_events` — and also returned under `run["context_manifest"]`.

        `timeout_seconds` defaults to 900s; pass `None` explicitly to disable
        the automatic timeout for this run.
        """
        context = context_service.assemble_context(
            project_id=project,
            metadata=metadata,
            candidate_content=candidate_content,
            confirmed_items=confirmed_items,
        )
        prompt = context_service.build_prompt(instruction, context)
        manifest = context_service.build_outbound_manifest(context)

        run = self.supervisor.start_raw(
            project=project,
            repository_path=repository_path,
            task_type=task_type,
            prompt=prompt,
            confirmed=confirmed,
            task_id=task_id,
            title=title or instruction[:120],
            session_id=session_id,
            is_resume=is_resume,
            model=model,
            timeout_seconds=timeout_seconds,
            expected_branch=expected_branch,
            launch_source=launch_source,
            prompt_version=prompt_version,
            repository_already_validated=repository_already_validated,
        )
        db.append_run_event(self.db_path, run["id"], "context_manifest", manifest)
        run = dict(run)
        run["context_manifest"] = manifest
        return run

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    def list_tasks(self, *, project: str | None = None) -> list[dict]:
        return db.list_tasks(self.db_path, project=project)

    def list_sessions(self, *, task_id: str | None = None) -> list[dict]:
        return db.list_sessions(self.db_path, task_id=task_id)

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        state: str | None = None,
        states: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        return db.list_runs(
            self.db_path,
            session_id=session_id,
            task_id=task_id,
            state=state,
            states=states,
            limit=limit,
        )

    def get_run(self, run_id: str) -> dict | None:
        return db.get_run(self.db_path, run_id)

    def get_events(self, run_id: str, *, after_seq: int = 0, limit: int = 1000) -> list[dict]:
        return db.list_run_events(self.db_path, run_id, after_seq=after_seq, limit=limit)

    def get_report(self, run_id: str) -> dict | None:
        return db.get_report(self.db_path, run_id)

    # ------------------------------------------------------------------
    # Completion pipeline (read-only projections + bounded advance)
    # ------------------------------------------------------------------

    def get_completion(self, run_id: str) -> dict | None:
        return db.get_completion(self.db_path, run_id)

    def get_completion_by_task(self, task_id: str) -> dict | None:
        return db.get_completion_by_task(self.db_path, task_id)

    def get_completion_events(self, run_id: str, *, limit: int = 500) -> list[dict]:
        return db.list_completion_events(self.db_path, run_id, limit=limit)

    def get_validation_results(self, run_id: str) -> list[dict]:
        return db.list_validation_results(self.db_path, run_id)

    def advance_completions(self, *, now=None, limit: int = 50, github=None) -> list:
        """Advance every due, non-terminal completion row once. Delegates to the
        Supervisor; safe to call on demand (bounded)."""
        return self.supervisor.advance_completions(now=now, limit=limit, github=github)

    # ------------------------------------------------------------------
    # Autonomy proposals (AICC-AUTONOMY-002) — the pre-execution decision
    # layer. The engine governs recommendation -> approval -> execution but
    # never executes: `dispatch_proposal` only records the boundary crossing
    # and returns a dry-run plan the caller must run via `start_run` itself.
    # ------------------------------------------------------------------

    @property
    def autonomy(self) -> autonomy_service.AutonomyEngine:
        engine = getattr(self, "_autonomy_engine", None)
        if engine is None:
            engine = autonomy_service.AutonomyEngine(self.db_path)
            self._autonomy_engine = engine
        return engine

    def create_proposal(
        self,
        *,
        kind: str,
        project: str,
        title: str,
        rationale: str,
        evidence: list | None = None,
        task_id: str | None = None,
        policy: "autonomy.AutonomyPolicy | None" = None,
    ) -> dict:
        return self.autonomy.create_proposal(
            kind=kind,
            project=project,
            title=title,
            rationale=rationale,
            evidence=evidence,
            task_id=task_id,
            policy=policy,
        )

    def assess_proposal(self, proposal_id: str, *, policy=None, now=None) -> dict:
        return self.autonomy.assess(proposal_id, policy=policy, now=now)

    def plan_proposal(self, proposal_id: str) -> dict:
        """Dry-run: describe what the proposal would do; execute nothing."""
        return self.autonomy.plan(proposal_id)

    def approve_proposal(self, proposal_id: str, *, actor: str, reason: str | None = None) -> dict:
        return self.autonomy.approve(proposal_id, actor=actor, reason=reason)

    def reject_proposal(self, proposal_id: str, *, actor: str, reason: str) -> dict:
        return self.autonomy.reject(proposal_id, actor=actor, reason=reason)

    def withdraw_proposal(self, proposal_id: str, *, actor: str, reason: str | None = None) -> dict:
        return self.autonomy.withdraw(proposal_id, actor=actor, reason=reason)

    def dispatch_proposal(self, proposal_id: str, *, actor: str, policy=None) -> dict:
        """Record that an APPROVED proposal has crossed the execution boundary
        and return its plan. Does not launch anything — the caller executes the
        plan explicitly via `start_run`, then calls `confirm_proposal_execution`."""
        return self.autonomy.dispatch(proposal_id, actor=actor, policy=policy)

    def confirm_proposal_execution(
        self, proposal_id: str, *, actor: str, run_id: str | None = None, task_id: str | None = None
    ) -> dict:
        return self.autonomy.confirm_execution(
            proposal_id, actor=actor, run_id=run_id, task_id=task_id
        )

    def get_proposal(self, proposal_id: str) -> dict | None:
        return self.autonomy.get(proposal_id)

    def list_proposals(self, *, project=None, states=None, kind=None, limit: int = 200) -> list[dict]:
        return self.autonomy.list(project=project, states=states, kind=kind, limit=limit)

    def get_proposal_evidence(self, proposal_id: str) -> list[dict]:
        return self.autonomy.evidence(proposal_id)

    def get_proposal_events(self, proposal_id: str, *, limit: int = 500) -> list[dict]:
        return self.autonomy.events(proposal_id, limit=limit)

    # ------------------------------------------------------------------
    # Cancellation — requires explicit confirmation from the caller/UI
    # ------------------------------------------------------------------

    def request_cancel(self, run_id: str, *, confirmed: bool, grace_seconds: float | None = None) -> dict:
        kwargs = {}
        if grace_seconds is not None:
            kwargs["grace_seconds"] = grace_seconds
        return self.supervisor.cancel(run_id, confirmed=confirmed, **kwargs)

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    def reconcile(self) -> list[dict]:
        return self.supervisor.reconcile()

    # ------------------------------------------------------------------
    # Context assembly (BANK/LEGAL sensitive-project boundary)
    # ------------------------------------------------------------------

    def assemble_context(
        self,
        *,
        project_id: str,
        metadata: dict | None = None,
        candidate_content: dict[str, str] | None = None,
        confirmed_items: list[str] | None = None,
    ) -> dict:
        return context_service.assemble_context(
            project_id=project_id,
            metadata=metadata,
            candidate_content=candidate_content,
            confirmed_items=confirmed_items,
        )
