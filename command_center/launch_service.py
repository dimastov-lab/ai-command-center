"""Launch orchestration as a plain-data service, fully decoupled from
Streamlit.

`app.py`'s `render_agent_launcher` collects widget input (project, prompt,
task type, timeout) and calls `execute_agent_launch`; nothing in this
module touches `st.*`, `session_state`, or any other Streamlit API. Per
`docs/desktop/ARCHITECTURE.md` §5 ("adapters... call existing functions
verbatim"), a future `command_center.application` adapter can call this
exact function from a `QRunnable` worker thread unchanged — it already
returns a plain dataclass, not a UI element, and every side effect
(subprocess execution, file writes) is delegated to already-existing,
already-tested `command_center/*` modules.

Routes execution through `command_center.executors` so the executor is a
parameter, not a hardcoded call to `agent_runner` — swapping in a future
ChatGPT/Codex/Gemini/remote executor requires no change here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from command_center import (
    activity_log,
    agent_runner,
    executors,
    git_info,
    launch,
    models,
    report_parser,
    workflow,
    workspace_provisioning,
)
from command_center.runtime import db as runtime_db


@dataclass
class LaunchOutcome:
    run: dict
    parsed: dict
    result_status: str
    report_relpath: str


# Decisions `prepare_task_launch` returns — the single classification both the
# Kanban launcher and the execution queue branch on, so neither reimplements
# the "is this launchable / provisionable / fatal?" logic.
LAUNCH_DECISION_READY = "ready"                      # workspace valid, launch now
LAUNCH_DECISION_NEEDS_CONFIRMATION = "needs_confirmation"  # valid but has warnings
LAUNCH_DECISION_PROVISIONABLE = "provisionable"      # workspace absent, can be created
LAUNCH_DECISION_BLOCKED = "blocked"                  # fatal — never launch


@dataclass
class LaunchPreparation:
    """The result of classifying a task launch *without mutating anything*.

    `decision` is one of the `LAUNCH_DECISION_*` constants. The remaining
    fields are the single resolved inputs every downstream launch step must
    use (never re-derived differently elsewhere): the workspace the agent
    will run in, the expected/base branches, the canonical source repository,
    and the (pre-provision) validation. `provision_notice` is a human-facing
    string for the `PROVISIONABLE` case; `fatal_messages` collects the
    blocking errors for the `BLOCKED` case."""

    decision: str
    selection: launch.WorkspaceSelection
    resolved_workspace: str | None
    expected_branch: str | None
    base_branch: str | None
    source_repository_path: str | None
    validation: launch.LaunchValidation
    provision_notice: str | None = None
    fatal_messages: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fatal_messages is None:
            self.fatal_messages = []

    @property
    def launchable(self) -> bool:
        """Whether the operator/queue may proceed to `execute_agent_launch_v2`
        — either the workspace is already valid, or it is absent but
        provisionable. The actual provisioning + the authoritative fail-closed
        verification still happen downstream (in `execute_agent_launch_v2` ->
        `provision_and_verify`, then again at `Supervisor.start_raw`); this
        only decides whether that path may be entered at all."""
        return self.decision in (
            LAUNCH_DECISION_READY,
            LAUNCH_DECISION_NEEDS_CONFIRMATION,
            LAUNCH_DECISION_PROVISIONABLE,
        )

    @property
    def provisionable(self) -> bool:
        return self.decision == LAUNCH_DECISION_PROVISIONABLE


def prepare_task_launch(*, task: dict | None, project_config: dict | None) -> LaunchPreparation:
    """Shared, **non-mutating** pre-launch classification for the normal task
    paths (Kanban launcher and execution queue). Resolves the single workspace
    / expected-branch / base-branch / source-repository inputs, runs
    `launch.validate_launch`, and classifies the result:

    - already valid                     -> READY / NEEDS_CONFIRMATION
    - absent but provisionable worktree -> PROVISIONABLE
    - anything else                     -> BLOCKED (fail closed)

    A workspace is *provisionable* only when validation's **sole** blocking
    issue is the stable `launch.ISSUE_WORKSPACE_MISSING` code (never matched
    from message text) **and** the provisioning inputs are sufficient: a real
    git source repository, an expected branch, a base branch that exists, and
    a feature/audit branch (isolated-worktree work — main-line branches are
    not auto-provisioned into a second worktree). Every other failure — no
    workspace configured, path is a file, not a git repo, missing/​unusable
    provisioning inputs — stays fatal. This function never creates a worktree;
    provisioning happens in `execute_agent_launch_v2` (which both callers
    funnel through) and is re-verified at the `Supervisor.start_raw`
    chokepoint."""
    selection = launch.resolve_workspace_path(task=task, project_config=project_config)
    expected_branch = launch.resolve_expected_branch(task=task, project_config=project_config)
    base_branch = launch.resolve_base_branch(task=task, project_config=project_config)
    source_repository_path = (project_config or {}).get("repository_path")

    validation = launch.validate_launch(workspace_path=selection.path, expected_branch=expected_branch)
    resolved_workspace = (
        str(Path(selection.path).expanduser().resolve()) if selection.path else None
    )

    if validation.can_launch:
        decision = (
            LAUNCH_DECISION_NEEDS_CONFIRMATION if validation.warnings else LAUNCH_DECISION_READY
        )
        return LaunchPreparation(
            decision=decision,
            selection=selection,
            resolved_workspace=resolved_workspace,
            expected_branch=expected_branch,
            base_branch=base_branch,
            source_repository_path=source_repository_path,
            validation=validation,
        )

    provisionable, reason = _classify_provisionable(
        validation=validation,
        expected_branch=expected_branch,
        base_branch=base_branch,
        source_repository_path=source_repository_path,
    )
    if provisionable:
        return LaunchPreparation(
            decision=LAUNCH_DECISION_PROVISIONABLE,
            selection=selection,
            resolved_workspace=resolved_workspace,
            expected_branch=expected_branch,
            base_branch=base_branch,
            source_repository_path=source_repository_path,
            validation=validation,
            provision_notice=reason,
        )

    return LaunchPreparation(
        decision=LAUNCH_DECISION_BLOCKED,
        selection=selection,
        resolved_workspace=resolved_workspace,
        expected_branch=expected_branch,
        base_branch=base_branch,
        source_repository_path=source_repository_path,
        validation=validation,
        fatal_messages=list(validation.errors),
    )


def _classify_provisionable(
    *,
    validation: launch.LaunchValidation,
    expected_branch: str | None,
    base_branch: str | None,
    source_repository_path: str | None,
) -> tuple[bool, str | None]:
    """Return `(True, notice)` when the only reason `validation` failed is a
    missing-but-provisionable workspace and the inputs to create it are
    present and usable, else `(False, None)`. Read-only."""
    if not validation.blocked_only_by(launch.ISSUE_WORKSPACE_MISSING):
        return False, None
    if not (source_repository_path and expected_branch and base_branch):
        return False, None
    if not workspace_provisioning.is_feature_task(expected_branch, base_branch):
        # A main-line branch is not auto-provisioned into a second worktree
        # (it is normally already checked out in the primary tree).
        return False, None

    repo = Path(source_repository_path).expanduser()
    if not git_info.get_status(repo).get("is_repo"):
        return False, None
    base_ref = git_info.run_git_command(repo, ["rev-parse", "--verify", f"{base_branch}^{{commit}}"])
    if base_ref is None or base_ref.returncode != 0:
        return False, None

    return True, (
        f"Изолированный worktree для ветки «{expected_branch}» будет создан автоматически "
        f"из «{base_branch}» перед запуском."
    )


def execute_agent_launch(
    *,
    project: str,
    task_type: str,
    prompt: str,
    timeout_seconds: int,
    repository_path: Path,
    task: dict | None = None,
    executor_id: str = "claude_code",
    validation: launch.LaunchValidation | None = None,
    on_task_state_changed: Callable[[], None] | None = None,
) -> LaunchOutcome:
    """Runs `executor_id` synchronously against `repository_path`, parses
    the resulting report, and — if `task` is given — updates it in place
    with every derived field (progress/stage/timeline/PR/verdict/launch
    status/history). Returns the run record and outcome for the caller to
    persist and display.

    `on_task_state_changed`, if given, is invoked synchronously right after
    each in-place task mutation that happens *before* the (potentially
    multi-minute) blocking executor call — the caller's hook to persist
    that intermediate state so a crash mid-run doesn't lose the "Launching"/
    "Running" marker. A plain callback rather than a return value or a
    direct write, so this module stays storage-agnostic (Streamlit's
    `save_tasks`, a future desktop adapter's own persistence call, or a
    test's no-op are all equally valid callers)."""

    if task is not None:
        models.push_prompt_history(task, prompt)
        if validation is not None:
            launch.begin_launch(task, executor_id=executor_id, validation=validation)
        if on_task_state_changed is not None:
            on_task_state_changed()

    task_id = (task or {}).get("id")

    run = models.new_run_record(
        project=project,
        task_id=task_id,
        agent=executor_id,
        task_type=task_type,
        repository_path=str(repository_path),
        prompt=prompt,
        timeout_seconds=timeout_seconds,
    )
    run["pre_run"] = agent_runner.git_snapshot(repository_path)
    agent_runner.append_run(run)
    activity_log.log_event(
        "run_queued", project=project, task_id=task_id, run_id=run["id"],
        message=f"Запуск {task_type} поставлен в очередь",
    )

    run["status"] = "running"
    agent_runner.append_run(run)
    activity_log.log_event(
        "run_started", project=project, task_id=task_id, run_id=run["id"], message="Запуск начат",
    )
    if task is not None:
        task["launch_status"] = "Running"
        models.append_timeline_event(task, "executor_started", f"{executor_id} запущен.")
        if on_task_state_changed is not None:
            on_task_state_changed()

    executor = executors.get_executor(executor_id)
    result = executor.launch(
        repository_path=repository_path,
        prompt=prompt,
        task_type=task_type,
        timeout_seconds=timeout_seconds,
        model=agent_runner.default_model(),
    )

    run["status"] = result.status
    run["exit_code"] = result.exit_code
    run["stdout"] = result.stdout
    run["stderr"] = result.stderr
    run["started_at"] = result.started_at
    run["completed_at"] = result.completed_at
    run["duration_seconds"] = result.duration_seconds
    run["post_run"] = agent_runner.git_snapshot(repository_path)

    report_text = agent_runner.extract_result_text(result.stdout) if result.stdout else ""
    parsed = report_parser.parse_report(report_text)
    run["parsed"] = parsed

    report_path = agent_runner.save_report(run, parsed)
    run["report_path"] = os.path.relpath(report_path, agent_runner.ROOT)
    agent_runner.append_run(run)

    activity_log.log_event(
        "run_completed" if result.status == "completed" else "run_failed",
        project=project, task_id=task_id, run_id=run["id"],
        message=f"Статус: {result.status}, exit_code={result.exit_code}",
    )
    if parsed.get("verdict"):
        activity_log.log_event(
            "verdict_extracted", project=project, task_id=task_id, run_id=run["id"],
            message=f"Вердикт: {parsed['verdict']}",
        )
    activity_log.log_event(
        "report_saved", project=project, task_id=task_id, run_id=run["id"], message=report_path.name,
    )

    if task is not None:
        _apply_run_outcome_to_task(task, run=run, result_status=result.status, parsed=parsed, executor_id=executor_id)

    return LaunchOutcome(
        run=run, parsed=parsed, result_status=result.status, report_relpath=run["report_path"]
    )


class DuplicateActiveLaunchError(Exception):
    """Raised when launching would create a second, concurrently-active v2
    run against the same task or the same resolved workspace — two agents
    mutating the same working tree at once, which nothing else in this
    codebase guards against (the old synchronous flow only avoided this by
    accident, by blocking the whole script for the run's duration)."""


def find_active_run_conflict(
    execution_center_api, *, task_id: str | None, resolved_workspace: str
) -> dict | None:
    """Returns the conflicting active run, if any — either one already
    bound to `task_id`, or a *different* task's run currently active
    against the exact same resolved workspace path. `None` means it is safe
    to launch."""
    for run in execution_center_api.list_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES):
        if task_id is not None and run.get("task_id") == task_id:
            return run
        run_repo = run.get("repository_path")
        if not run_repo:
            continue
        try:
            if str(Path(run_repo).expanduser().resolve()) == resolved_workspace:
                return run
        except OSError:
            continue
    return None


def execute_agent_launch_v2(
    *,
    project: str,
    task_type: str,
    prompt: str,
    timeout_seconds: int,
    repository_path: Path,
    execution_center_api,
    confirmed: bool,
    task: dict | None = None,
    executor_id: str = "claude_code",
    validation: launch.LaunchValidation | None = None,
    expected_branch: str | None = None,
    base_branch: str | None = None,
    source_repository_path: str | None = None,
    status_policy: str = workspace_provisioning.STATUS_POLICY_ALLOW_DIRTY,
    provision_workspace: bool = True,
    on_task_state_changed: Callable[[], None] | None = None,
) -> dict:
    """Async counterpart to `execute_agent_launch` above — same pre-launch
    bookkeeping (`push_prompt_history`, `launch.begin_launch`), but instead
    of blocking on a synchronous executor call, starts a real, PID-tracked,
    cancellable v2 run via `ExecutionCenterAPI.start_run` and returns
    immediately in state `RUNNING`/`QUEUED`. The caller (`app.py`) redirects
    the user to Live Execution Center to watch it, instead of blocking
    behind `st.spinner`.

    Raises `DuplicateActiveLaunchError` — before any task mutation, and
    before any subprocess is spawned — if the task already has an active
    run, or if any other active run is already using this exact resolved
    workspace.

    When `provision_workspace` is true (the default), the resolved workspace
    (`repository_path`) is provisioned if absent and then verified against
    `expected_branch`/`base_branch`/`source_repository_path`/`status_policy`
    via `command_center.workspace_provisioning` *before* any task state is
    mutated. A failed check raises a structured
    `workspace_provisioning.WorkspaceVerificationError` (here) or
    `supervisor.WorkspaceVerificationFailed` (at the `start_raw` chokepoint) —
    the run never starts and the workspace never silently falls back to the
    source repository. `source_repository_path` is the project's canonical
    repository (used to prove the worktree belongs to it and to create it);
    `base_branch` is where a new feature/audit worktree is forked from.

    `execute_agent_launch` (old, synchronous) is left completely untouched —
    this is a pure addition, not a replacement, so anything not yet bridged
    onto v2 keeps working exactly as before.
    """
    task_id = (task or {}).get("id")
    resolved_workspace = str(Path(repository_path).expanduser().resolve())
    conflict = find_active_run_conflict(
        execution_center_api, task_id=task_id, resolved_workspace=resolved_workspace
    )
    if conflict is not None:
        raise DuplicateActiveLaunchError(
            f"An active run (`{conflict['id']}`, state={conflict['state']!r}) already exists for "
            f"this task or workspace (`{resolved_workspace}`). Wait for it to finish or cancel it "
            "before launching again."
        )

    # Provision + verify the isolated worktree *before* any task state is
    # mutated (so "Workspace Verified" — set by `launch.begin_launch` below —
    # is never reached on a failed workspace) and *before* the run is started.
    # The same spec is forwarded to `start_run` -> `start_raw`, whose gate is
    # the authoritative fail-closed chokepoint; verifying here as well means a
    # bad workspace is rejected with zero side effects, not after a run row and
    # activity-log entries already exist. Raises
    # `workspace_provisioning.WorkspaceVerificationError` (structured) — or, at
    # the `start_raw` gate, `supervisor.WorkspaceVerificationFailed` — on any
    # failed check; neither is caught here, so a failed launch never proceeds.
    workspace_verification: workspace_provisioning.WorkspaceSpec | None = None
    if provision_workspace:
        workspace_verification = workspace_provisioning.WorkspaceSpec(
            workspace_path=str(repository_path),
            expected_branch=expected_branch,
            base_branch=base_branch,
            repository_path=source_repository_path,
            task_type=task_type,
            status_policy=status_policy,
        )
        workspace_provisioning.provision_and_verify(workspace_verification)
        # Post-provision validation: if the caller handed us a validation that
        # was only failing because the workspace did not exist yet (the
        # provisionable path — see `prepare_task_launch`), it is now stale. The
        # worktree exists and has just passed the full isolation gate, so
        # refresh it against the real, now-present workspace so `begin_launch`
        # below records accurate branch/status instead of the pre-provision
        # "not found" state. A genuinely fatal validation never reaches here
        # (the caller blocks first) and would in any case have already raised
        # from `provision_and_verify`.
        if validation is not None and not validation.can_launch:
            validation = launch.validate_launch(
                workspace_path=str(repository_path), expected_branch=expected_branch
            )

    if task is not None:
        models.push_prompt_history(task, prompt)
        if validation is not None:
            launch.begin_launch(task, executor_id=executor_id, validation=validation)
        if on_task_state_changed is not None:
            on_task_state_changed()

    title = (task or {}).get("title") or prompt[:120]

    run = execution_center_api.start_run(
        project=project,
        repository_path=str(repository_path),
        task_type=task_type,
        instruction=prompt,
        confirmed=confirmed,
        task_id=task_id,
        title=title,
        timeout_seconds=timeout_seconds,
        expected_branch=expected_branch,
        launch_source="kanban_task" if task is not None else "execution_center_adhoc",
        prompt_version=(task or {}).get("prompt_version"),
        # `repository_path` here is exactly the path `validation` was
        # computed against (the caller's contract — see `render_agent_
        # launcher`), already checked for existence/is-a-directory/is-a-
        # git-repo by `launch.validate_launch` — equivalent-or-stronger than
        # `agent_runner.validate_repository`'s project-repository-equality
        # check, which would otherwise reject a task's own worktree on a
        # different path. Gated on `validation.can_launch`, not just
        # `validation is not None` — a `validation` object that itself
        # failed (missing workspace, not a git repo) must never bypass the
        # stricter check just because *a* validation object was passed in;
        # defense-in-depth against a future caller that forwards a failed
        # validation without re-checking it first (today's only caller,
        # `render_agent_launcher`, already refuses to reach this call at all
        # when `not validation.can_launch`, but this must hold regardless of
        # caller discipline).
        # A passed `workspace_verification` is a strictly stronger check than
        # `agent_runner.validate_repository`'s project-repo-equality guard (it
        # proves an isolated worktree of the configured repo on the expected
        # branch), and it would *reject* a task's own worktree — whose path is
        # deliberately not the project's configured `repository_path`. So a
        # provisioned+verified workspace also counts as already-validated.
        repository_already_validated=bool(
            (validation is not None and validation.can_launch) or workspace_verification is not None
        ),
        # Re-verified at the `start_raw` chokepoint (fail-closed) — no launch
        # path, present or future, can spawn the process without passing this.
        workspace_verification=workspace_verification,
    )

    if task is not None:
        task["current_run_id"] = run["id"]
        if on_task_state_changed is not None:
            on_task_state_changed()

    activity_log.log_event(
        "run_queued", project=project, task_id=task_id, run_id=run["id"],
        message=f"Асинхронный запуск {task_type} через Live Execution Center v2 (run {run['id'][:8]})",
    )

    return run


def _apply_run_outcome_to_task(
    task: dict, *, run: dict, result_status: str, parsed: dict, executor_id: str
) -> None:
    task["current_run_id"] = run["id"]
    task["latest_verdict"] = parsed.get("verdict")
    task["report_path"] = run["report_path"]
    task["repository_path"] = run["repository_path"]
    task["branch"] = run["post_run"].get("branch")
    task["agent"] = executor_id
    task["executor"] = executor_id
    task["last_run_at"] = run["completed_at"]
    if parsed.get("pull_request_url"):
        task["pull_request_url"] = parsed["pull_request_url"]

    models.append_timeline_event(task, "validation_started", "Проверка результатов запуска.")

    if result_status == "completed":
        suggestion = workflow.suggest_next_task(run)
        task["workflow_stage"] = suggestion["workflow_stage"]
        if parsed.get("pull_request_url"):
            models.set_current_stage(task, "PR Ready")
            models.append_timeline_event(task, "pr_created", parsed["pull_request_url"])
        elif models.is_passing_verdict(parsed.get("verdict")):
            models.set_current_stage(task, "Validation")
            models.append_timeline_event(task, "tests_passed", f"Вердикт: {parsed['verdict']}")
        else:
            models.set_current_stage(task, "Coding Complete")
    else:
        task["workflow_stage"] = "Ready"

    launch.complete_launch(
        task,
        executor_id=executor_id,
        succeeded=result_status == "completed",
        workspace_path=run["repository_path"],
    )
    task["updated_at"] = models.iso_now()
