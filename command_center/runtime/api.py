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

from command_center.runtime import context_service, db, scheduler, supervisor

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
    # Scheduling (read-only decision layer — never launches anything here)
    # ------------------------------------------------------------------

    def plan_schedule(
        self,
        work_items: list[scheduler.WorkItem],
        *,
        registry: scheduler.AgentRegistry | None = None,
        config: scheduler.SchedulerConfig | None = None,
        policy: scheduler.RetryPolicy | None = None,
        now: str | None = None,
    ) -> scheduler.SchedulingPlan:
        """Deterministically plan which `work_items` should be assigned,
        deferred, or blocked *right now*, against the live in-flight load read
        from this API's own `runtime.db`. Pure decision only — this returns a
        `SchedulingPlan` and launches nothing. Acting on an `ASSIGN` decision
        is a separate, explicit `start_run` call by the caller, so the launch
        confirmation / sensitive-content boundary is never bypassed here."""
        load = scheduler.build_load_snapshot(self.db_path)
        return scheduler.plan(
            work_items,
            registry=registry or scheduler.default_registry(),
            load=load,
            config=config,
            policy=policy,
            now=now,
        )

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
