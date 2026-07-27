"""One-way, conservative sync from a v2 `run` (the execution-state source of
truth) onto the Kanban task it was launched for.

Never re-derives execution state from a task field — always reads the
`run` row fresh via `ExecutionCenterAPI`/`db`. Touches `launch_status` and
the small set of display/bookkeeping fields (`current_run_id`,
`report_path`, `repository_path`, `branch`, `last_run_at`,
`latest_verdict`, `pull_request_url`) that already exist on every task
record — and, as of this remediation, also `current_stage`/`progress` for a
genuinely `Completed` run's terminal sync (see `_apply_terminal_fields`),
mirroring the exact `models.set_current_stage` calls the v1 synchronous
flow (`launch_service._apply_run_outcome_to_task`) already makes. Before
this fix, progress/stage were *never* advanced for a v2-launched task: a
task launched once (which sets stage to "Workspace Verified", progress 5 —
see `launch.begin_launch`) stayed frozen at 5% forever, even after its run
reached `launch_status="Completed"` — a direct violation of "Completed
implies progress == 100" and the literal shape of the reported defect
(`state=COMPLETED`, `progress` stuck at 5%).
"""

from __future__ import annotations

from command_center import models, project_config, report_parser
from command_center.runtime import completion as completion_states
from command_center.runtime import db, reports, session_view
from command_center.runtime.api import ExecutionCenterAPI
from command_center.runtime.completion_service import CompletionOrchestrator

# Session-view display status -> Kanban `models.LAUNCH_STATUSES` value, for
# every status *except* `STATUS_COMPLETED` — a genuinely-completed run does
# not automatically become launch_status "Completed"; see
# `_resolve_target_launch_status` for why (progress must reach 100 first,
# per the "Completed implies progress == 100" invariant — otherwise the
# task is "Needs Review": the agent's work is done, but nothing has
# reviewed/merged it yet).
_LAUNCH_STATUS_BY_DISPLAY_STATUS: dict[str, str] = {
    session_view.STATUS_LAUNCHING: "Launching",
    # `Starting` (spawned, awaiting first output) and `Stale` (spawned, probe
    # momentarily old) are *live, non-terminal* states — the process exists.
    # They map to the active Kanban launch statuses ("Launching"/"Running"),
    # never to a terminal one: a successfully spawned run must never flip its
    # task to Failed/Requires-Attention merely because early output or a fresh
    # probe has not arrived. Because neither is in
    # `session_view.TERMINAL_DISPLAY_STATUSES`, `_apply_terminal_fields` never
    # runs for them either.
    session_view.STATUS_STARTING: "Launching",
    session_view.STATUS_RUNNING: "Running",
    session_view.STATUS_STALE: "Running",
    session_view.STATUS_WAITING: "Requires Attention",
    session_view.STATUS_REQUIRES_ATTENTION: "Requires Attention",
    session_view.STATUS_BLOCKED: "Blocked",
    session_view.STATUS_INCOMPLETE: "Incomplete",
    # `models.LAUNCH_STATUSES` has no dedicated "Cancelled" value — the
    # closest existing one is "Failed" (the task did not finish its work),
    # documented as a known simplification (see the plan's Limitations).
    session_view.STATUS_FAILED: "Failed",
    session_view.STATUS_CANCELLED: "Failed",
}

_TERMINAL_LAUNCH_STATUSES = frozenset({"Completed", "Needs Review", "Failed", "Blocked", "Incomplete"})


def _resolve_target_launch_status(status: str, task: dict) -> str:
    """`status` is the process-level `session_view.derive_status` result.
    Every non-`Completed` status maps 1:1 via `_LAUNCH_STATUS_BY_DISPLAY_
    STATUS`. `Completed` is resolved dynamically against the task's *current*
    `progress` (already advanced by `_apply_terminal_fields`, which always
    runs — for a terminal, not-yet-finalized run — before this is called):
    `progress == 100` is "Merged" (see `models.STAGE_PROGRESS`), the only
    stage this codebase's agent-safety model ever lets an *agent* run reach
    on its own is "PR Ready" (95) — reaching "Merged" requires a human/
    process action this project's agents are never permitted to take (no
    run here ever calls `git commit`/`push`/`merge`). So a successful run
    is "Needs Review" (PR ready, awaiting human review/merge) rather than
    "Completed" until that happens — this is what makes "Completed implies
    progress == 100" a real, permanently-held invariant instead of a
    tautology that's vacuously true because nothing ever reaches it."""
    if status != session_view.STATUS_COMPLETED:
        return _LAUNCH_STATUS_BY_DISPLAY_STATUS[status]
    # A read-only task (review/audit/gate) has no merge/review lifecycle: its
    # report is the deliverable, so a clean `COMPLETED` is terminally "Completed",
    # not "Needs Review" (there is no PR to review) and never "Requires Attention"
    # (there is nothing to merge, so no completion pipeline runs for it — see
    # `_seed_and_project_completion`).
    if session_view.is_read_only_task_type(task.get("task_type")):
        return "Completed"
    return "Completed" if (task.get("progress") or 0) >= 100 else "Needs Review"


def _apply_terminal_fields(task: dict, run: dict, *, status: str, db_path) -> None:
    """The one-time work done exactly once per terminal run, guarded by the
    caller (`sync_task_from_run`). Reads the run's final result text via the
    same deterministic `report_parser.parse_report` v1.2 already uses — no
    new extraction logic. `status` is the already-computed
    `session_view.derive_status(run)` value, passed in rather than re-read
    from `task["launch_status"]` (which has not been updated for this sync
    yet — see `sync_task_from_run`'s ordering)."""
    report_row = db.get_report(db_path, run["id"])
    if report_row:
        task["report_path"] = report_row["path"]

    task["repository_path"] = run.get("repository_path")
    live_status = session_view.live_git_status(run.get("repository_path"))
    task["branch"] = (live_status or {}).get("branch") or run.get("expected_branch") or task.get("branch")
    task["last_run_at"] = run.get("completed_at")

    events = db.list_run_events(db_path, run["id"], after_seq=0, limit=1_000_000)
    parsed = report_parser.parse_report(reports.result_text(events)) if events else report_parser.empty_parsed_result()
    if parsed.get("verdict"):
        task["latest_verdict"] = parsed["verdict"]
    if parsed.get("pull_request_url"):
        task["pull_request_url"] = parsed["pull_request_url"]

    commit_hash = parsed.get("commit_hash")
    pull_request_url = parsed.get("pull_request_url")
    if commit_hash or pull_request_url:
        try:
            db.set_run_result_fields(
                db_path,
                run["id"],
                expected_version=run["version"],
                commit_hash=commit_hash,
                pull_request_url=pull_request_url,
            )
        except db.LostUpdateError:
            pass  # cosmetic run-row enrichment only; the task fields above are already set

    # Advance `current_stage`/`progress` for a genuinely completed run —
    # same rule v1's `launch_service._apply_run_outcome_to_task` already
    # uses. Never for `Blocked`/`Incomplete`/`Failed`/`Cancelled`: none of
    # those represent real forward progress, so `progress` must stay exactly
    # where it was (whatever stage the task was actually verified to reach),
    # not be nudged forward by a run that didn't deliver.
    if status == session_view.STATUS_COMPLETED:
        if pull_request_url:
            models.set_current_stage(task, "PR Ready")
            models.append_timeline_event(task, "pr_created", pull_request_url)
        elif models.is_passing_verdict(parsed.get("verdict")):
            models.set_current_stage(task, "Validation")
            models.append_timeline_event(task, "tests_passed", f"Вердикт: {parsed.get('verdict')}")

    event_type = "completed" if status == session_view.STATUS_COMPLETED else "launch_requires_attention"
    models.append_timeline_event(
        task, event_type, f"Синхронизировано из прогона `{run['id'][:8]}` (Live Execution Center v2)."
    )


def sync_task_from_run(task: dict, run: dict, *, db_path) -> bool:
    """Returns whether `task` was mutated (so the caller knows to persist
    via `save_tasks`)."""
    status = session_view.derive_status(run)
    mutated = False

    is_new_run_for_task = task.get("current_run_id") != run["id"]
    already_finalized_for_this_run = (
        not is_new_run_for_task and task.get("launch_status") in _TERMINAL_LAUNCH_STATUSES
    )

    if is_new_run_for_task:
        task["current_run_id"] = run["id"]
        mutated = True

    if status in session_view.TERMINAL_DISPLAY_STATUSES and not already_finalized_for_this_run:
        # Must run *before* `target_launch_status` is resolved below: a
        # `Completed` run's launch status depends on `task["progress"]`
        # *after* this call's own stage advancement, not before it.
        _apply_terminal_fields(task, run, status=status, db_path=db_path)
        mutated = True

    target_launch_status = _resolve_target_launch_status(status, task)

    # --- Executor fallback auto-retry (AICC-DESKTOP-017) -----------------
    # A run that died on startup (INTERRUPTED, never produced output) is a
    # strong signal the executor itself is broken — e.g. an expired OAuth
    # token that `provider.availability()` (which only probes `--version`)
    # cannot detect. Instead of stranding the task as "Requires Attention",
    # record the failed executor and flip the task back to "Ready" so the
    # next launch automatically tries the next available agent in the chain
    # (see `execution_queue.select_available_executor`). The task only
    # reaches "Requires Attention" when *every* allowed executor has failed.
    if (
        run.get("state") == "INTERRUPTED"
        and not run.get("first_output_at")
        and not already_finalized_for_this_run
    ):
        failed_executor = run.get("provider_id") or task.get("executor") or "claude_code"
        failed_list = list(task.get("failed_executors") or [])
        if failed_executor not in failed_list:
            failed_list.append(failed_executor)
            task["failed_executors"] = failed_list
            mutated = True
        # Keep the task re-launchable so the next `launch_ready` picks up the
        # next executor in the chain. Only strand it as "Requires Attention"
        # when there are no more agents to try.
        if target_launch_status == "Requires Attention":
            target_launch_status = "Ready"
            models.append_timeline_event(
                task,
                "executor_failed",
                f"Исполнитель «{failed_executor}» не запустился (нет вывода). "
                "Задача возвращена в очередь для повтора другим агентом.",
            )
            mutated = True

    # On a clean completion, clear any accumulated executor failures — the
    # chain is healthy again and a future re-launch should try the configured
    # executor from scratch.
    if status == session_view.STATUS_COMPLETED and task.get("failed_executors"):
        task.pop("failed_executors", None)
        mutated = True

    if task.get("launch_status") != target_launch_status:
        task["launch_status"] = target_launch_status
        mutated = True

    if mutated:
        task["updated_at"] = models.iso_now()
    return mutated


# Completion-pipeline state -> Kanban `launch_status`. Once a run has a
# completion row, the completion pipeline (not the raw run outcome) governs the
# user-facing status: a finished process is "Needs Review" until something
# actually advances it, "Running" only while the pipeline is *actively*
# advancing (validating), "Needs Review" while a PR is open/merging/finalizing,
# and only "Completed" once the change is verified in the target branch. Closed-
# unmerged / validation-failed / blocked all surface as "Requires Attention".
#
# `EXECUTION_FINISHED` is the seed state every completion row is born in. It
# means the Claude *process* has exited (a completion row is only ever created
# for a terminal, `COMPLETED` run) — it is NOT evidence of any live activity.
# With the completion autopilot disabled (the default; see
# `Supervisor.start_completion_autopilot`), nothing advances the row past this
# seed, so mapping it to "Running" would strand a finished, PR-ready task as
# perpetually "Running" — a persisted, user-visible regression that conflates
# execution activity with engineering-lifecycle progress. It therefore projects
# to the honest, actionable "Needs Review" (matching the raw-run projection a
# completed run already resolves to), preserving default behavior. When the
# autopilot IS enabled, the row leaves `EXECUTION_FINISHED` within a tick and
# surfaces the genuinely-active "Running"/"Needs Review" states below.
_LAUNCH_STATUS_BY_COMPLETION_STATE: dict[str, str] = {
    completion_states.CompletionState.EXECUTION_FINISHED: "Needs Review",
    completion_states.CompletionState.VALIDATING_RESULT: "Running",
    completion_states.CompletionState.RESULT_VALID: "Running",
    completion_states.CompletionState.PREPARING_PULL_REQUEST: "Running",
    completion_states.CompletionState.RECOVERY_PENDING: "Running",
    # Waiting on the blocking independent review: honest "Needs Review" — the
    # work is done and something is judging it.
    completion_states.CompletionState.AWAITING_REVIEW: "Needs Review",
    completion_states.CompletionState.PULL_REQUEST_OPEN: "Needs Review",
    completion_states.CompletionState.AWAITING_MERGE: "Needs Review",
    completion_states.CompletionState.MERGED: "Needs Review",
    completion_states.CompletionState.VERIFYING_TARGET_BRANCH: "Needs Review",
    completion_states.CompletionState.COMPLETED: "Completed",
    completion_states.CompletionState.VALIDATION_FAILED: "Requires Attention",
    completion_states.CompletionState.REVIEW_REJECTED: "Requires Attention",
    completion_states.CompletionState.PR_CLOSED_UNMERGED: "Requires Attention",
    completion_states.CompletionState.MERGE_BLOCKED: "Requires Attention",
    completion_states.CompletionState.REQUIRES_ATTENTION: "Requires Attention",
    completion_states.CompletionState.RECOVERY_FAILED: "Requires Attention",
}


def sync_task_from_completion(task: dict, completion: dict) -> bool:
    """Project a completion row onto its task. Once seeded, the completion
    pipeline is the authority for `launch_status`; on terminal success it also
    advances the task to stage "Merged"/progress 100 and sets
    `pull_request_status="merged"` (the field `recommend.py` reads for
    dependency gating). Returns whether the task was mutated."""
    state = completion["completion_state"]
    mutated = False

    pr_url = completion.get("pull_request_url")
    if pr_url and task.get("pull_request_url") != pr_url:
        task["pull_request_url"] = pr_url
        mutated = True

    if state == completion_states.CompletionState.COMPLETED:
        if (task.get("progress") or 0) < 100:
            models.set_current_stage(task, "Merged")
            models.append_timeline_event(task, "completed", "Merged into target branch (completion pipeline).")
            mutated = True
        if task.get("pull_request_status") != "merged":
            task["pull_request_status"] = "merged"
            mutated = True

    target = _LAUNCH_STATUS_BY_COMPLETION_STATE.get(state)
    if target and task.get("launch_status") != target:
        task["launch_status"] = target
        mutated = True

    if mutated:
        task["updated_at"] = models.iso_now()
    return mutated


def project_completion_to_kanban(task: dict, completion: dict) -> bool:
    """The final, planning-lane half of the completion projection: move the
    Kanban task itself to `Done` once — and only once — the completion pipeline
    has *verified* the merge landed in the target branch.

    Kept separate from `sync_task_from_completion` (which owns the execution-
    facing `launch_status`/stage/progress fields) because these are two
    genuinely different assertions with different consequences. `launch_status
    == "Completed"` says "this run's lifecycle finished"; `status == "Done"` is
    what `models.unmet_dependencies` reads, so setting it *releases every
    dependent task for execution*. That is the single most consequential field
    write in the whole pipeline, and it is deliberately gated on
    `CompletionState.COMPLETED` — the state reached only after
    `VERIFYING_TARGET_BRANCH` confirms the commit is reachable from the base
    branch. A PR that is merely open, or merged-but-unverified, never unblocks
    anything.

    Returns whether `task` was mutated."""
    if completion.get("completion_state") != completion_states.CompletionState.COMPLETED:
        return False
    if task.get("status") == "Done":
        return False
    task["status"] = "Done"
    models.append_timeline_event(
        task, "completed", "Задача закрыта: merge подтверждён в целевой ветке."
    )
    task["updated_at"] = models.iso_now()
    return True


def _seed_and_project_completion(
    api: ExecutionCenterAPI, task: dict, run: dict, *, policy_overrides: dict | None = None
) -> bool:
    """Ensure a completion row exists for a terminally-completed run (seeding is
    cheap and DB-only — it never runs validation or touches GitHub; that
    happens in the Supervisor's completion autopilot), then project its state
    onto the task. No-op for runs that did not complete successfully.

    `policy_overrides` is forwarded to `begin_completion` and therefore applies
    only to a row being created right now (see that method's docstring)."""
    # A read-only task (review/audit/gate) has no merge lifecycle — its report is
    # the deliverable. The completion pipeline is a validate → PR → merge → verify
    # state machine; running it for an analysis has nothing to do and spuriously
    # lands in MERGE_BLOCKED, which then projects the task to "Requires Attention"
    # even though the run succeeded. So a read-only run never seeds a completion,
    # and any completion left over from before this guard is ignored: the run's
    # own terminal projection (`_resolve_target_launch_status` → "Completed")
    # already says the true, terminal outcome.
    task_type = run.get("task_type") or task.get("task_type")
    if session_view.is_read_only_task_type(task_type):
        return False

    completion = db.get_completion(api.db_path, run["id"])
    if completion is None:
        if run.get("state") != "COMPLETED":
            return False
        cfg = project_config.get_project_config(run["project"])
        completion = CompletionOrchestrator(api.db_path).begin_completion(
            run, task=task, project_cfg=cfg, policy_overrides=policy_overrides
        )
        if completion is None:
            return False
    return sync_task_from_completion(task, completion)


def sync_tasks(
    api: ExecutionCenterAPI,
    tasks: list[dict],
    *,
    policy_overrides: dict | None = None,
) -> list[dict]:
    """Sync every task that has a `current_run_id` (i.e. was launched, at some
    point, through the v2 bridge) against that run's current state, seeding and
    projecting its completion row. Tasks never touched by v2 launch have no
    `current_run_id` and are left completely alone. Returns the mutated tasks
    for the caller to persist.

    Split out of `reconcile_and_sync` so a caller that has *already* reconciled
    — the desktop pipeline runs reconcile, then a completion advance, then this
    — can order those steps itself without paying for a second redundant
    `Supervisor.reconcile()`. `reconcile_and_sync` remains the one-call form for
    every existing caller."""
    mutated: list[dict] = []
    for task in tasks:
        run_id = task.get("current_run_id")
        if not run_id:
            continue
        run = api.get_run(run_id)
        if run is None:
            continue
        changed = sync_task_from_run(task, run, db_path=api.db_path)
        # Seed + project the completion pipeline (post-execution lifecycle).
        if _seed_and_project_completion(api, task, run, policy_overrides=policy_overrides):
            changed = True
        if changed:
            mutated.append(task)
    return mutated


def reconcile_and_sync(api: ExecutionCenterAPI, tasks: list[dict]) -> list[dict]:
    """Reconciles every persisted `RUNNING` row against real OS processes
    (`Supervisor.reconcile()`, already implemented and tested — never
    duplicated here), then syncs every launched task against its run's current
    state via `sync_tasks`. Returns the mutated tasks for the caller to
    persist."""
    api.reconcile()
    return sync_tasks(api, tasks)
