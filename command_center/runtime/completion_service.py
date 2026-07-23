"""Orchestration for the autonomous completion pipeline — the restart-safe,
idempotent advancer that turns the pure `CompletionEvaluator`'s verdicts into
real side effects (running validation, pushing branches, opening/merging PRs,
verifying the target branch) and persists every step.

Design guarantees:
  * **Restart-safe** — all state lives in the `completion` table; an advance is
    a function of the persisted row plus freshly-observed git/GitHub state, so a
    crash between any two steps resumes correctly.
  * **Idempotent** — the completion row itself (one per run, PK on run_id) is
    the anti-duplication guard; PR creation is always preceded by discovery, so
    a replacement PR is never opened twice even across a restart mid-recovery.
  * **Bounded** — `advance_pending_completions` processes only *due* rows (retry
    backoff via `next_retry_at`) in non-terminal states, up to a limit, and
    never blocks: it performs at most a bounded number of steps per row.
  * **No terminal reprocessing** — terminal completion states are never advanced
    again.

This module performs git *writes* and `gh` calls; it is deliberately the one
privileged actor that may push/merge (agents never can). It never force-pushes,
never deletes branches, and never merges when checks fail or a required review
is absent — those rules are enforced by the evaluator it consults.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from command_center.runtime import db as runtime_db
from command_center.runtime import git_ops, repo_state, validation
from command_center.runtime.completion import (
    CompletionAction,
    CompletionAssessment,
    CompletionEvaluator,
    CompletionPolicy,
    CompletionState,
    ReasonCode,
)
from command_center.runtime.github import GitHubClient, GitHubError, PullRequestState

# Audit event types (spec §14).
EV_EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
EV_VALIDATION_STARTED = "VALIDATION_STARTED"
EV_VALIDATION_PASSED = "VALIDATION_PASSED"
EV_VALIDATION_FAILED = "VALIDATION_FAILED"
EV_REMOTE_BRANCH_MISSING = "REMOTE_BRANCH_MISSING"
# Ordinary first-time publication of a branch that never existed on the remote
# (the normal open-PR path). Distinct from `REMOTE_BRANCH_RECREATED`, which is
# reserved for actually re-pushing a branch during closed-unmerged *recovery*.
EV_REMOTE_BRANCH_PUBLISHED = "REMOTE_BRANCH_PUBLISHED"
EV_REMOTE_BRANCH_RECREATED = "REMOTE_BRANCH_RECREATED"
EV_PR_CREATED = "PR_CREATED"
EV_PR_CLOSED_UNMERGED = "PR_CLOSED_UNMERGED"
EV_REPLACEMENT_PR_CREATED = "REPLACEMENT_PR_CREATED"
EV_MERGE_STARTED = "MERGE_STARTED"
EV_PR_MERGED = "PR_MERGED"
EV_TARGET_BRANCH_VERIFIED = "TARGET_BRANCH_VERIFIED"
EV_TASK_COMPLETED = "TASK_COMPLETED"
EV_REQUIRES_ATTENTION = "REQUIRES_ATTENTION"
EV_RECOVERY_FAILED = "RECOVERY_FAILED"
EV_STATE_CHANGED = "STATE_CHANGED"
# A same-state backoff re-check was scheduled (no state change). Kept distinct
# from `STATE_CHANGED` so the audit trail never claims a transition happened
# when only `next_retry_at`/`retry_count` moved.
EV_RETRY_SCHEDULED = "RETRY_SCHEDULED"

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
_MAX_STEPS_PER_ADVANCE = 8
DEFAULT_REMOTE = "origin"
DEFAULT_BASE_BRANCH = "main"


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _compute_next_retry(now: datetime, retry_count: int) -> str:
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** min(retry_count, 12)), _BACKOFF_CAP_SECONDS)
    return _iso(now + timedelta(seconds=delay))


@dataclass
class AdvanceResult:
    run_id: str
    from_state: str
    to_state: str
    reason_code: str
    changed: bool


class CompletionOrchestrator:
    """Advances completion rows. Dependencies (GitHub client, evaluator) are
    injected so tests can substitute in-memory doubles and a fixed clock."""

    def __init__(
        self,
        db_path: Path,
        *,
        github: object | None = None,
        evaluator: CompletionEvaluator | None = None,
    ) -> None:
        self.db_path = db_path
        self.github = github if github is not None else GitHubClient()
        self.evaluator = evaluator or CompletionEvaluator()

    # -- seeding ------------------------------------------------------------------
    def begin_completion(
        self,
        run: dict,
        *,
        task: dict | None = None,
        project_cfg: dict | None = None,
        now: datetime | None = None,
    ) -> dict | None:
        """Create the completion row for a terminally-`COMPLETED` run, if one
        does not already exist. Returns the row (existing or new), or None if
        the run is not eligible (not a successful execution).

        Idempotent: a second call for the same run returns the existing row and
        never creates a duplicate (the PK on run_id guarantees it)."""
        run_id = run["id"]
        existing = runtime_db.get_completion(self.db_path, run_id)
        if existing is not None:
            return existing
        if run.get("state") != "COMPLETED":
            return None

        policy = CompletionPolicy.resolve(task=task, project_cfg=project_cfg)
        repo = run["repository_path"]
        base_branch = (project_cfg or {}).get("default_branch") or DEFAULT_BASE_BRANCH
        # Head branch: prefer the live worktree branch, fall back to the run's
        # resolved expected branch.
        head_branch = repo_state.current_branch(Path(repo)) or run.get("expected_branch")
        head = repo_state.head_commit(Path(repo))
        row = runtime_db.create_completion(
            self.db_path,
            run_id=run_id,
            task_id=run["task_id"],
            project=run["project"],
            repository_path=repo,
            completion_state=CompletionState.EXECUTION_FINISHED,
            session_id=run.get("session_id"),
            branch=head_branch,
            base_branch=base_branch,
            head_commit=head,
            remote=DEFAULT_REMOTE,
            merge_mode=policy.merge_mode,
            merge_method=policy.merge_method,
            policy_json=policy.to_json(),
            last_reason_code=ReasonCode.EXECUTION_OK,
        )
        runtime_db.append_completion_event(
            self.db_path,
            run_id,
            EV_EXECUTION_COMPLETED,
            reason_code=ReasonCode.EXECUTION_OK,
            message="Claude execution completed; beginning completion pipeline.",
            metadata={"branch": head_branch, "base_branch": base_branch, "head_commit": head},
        )
        return row

    # -- advancing ----------------------------------------------------------------
    def advance(self, run_id: str, *, now: datetime | None = None) -> AdvanceResult:
        """Advance a single completion row as far as it can go without waiting
        (bounded by `_MAX_STEPS_PER_ADVANCE`). Safe to call on any row,
        including terminal ones (returns unchanged)."""
        now = now or _now()
        row = runtime_db.get_completion(self.db_path, run_id)
        if row is None:
            raise KeyError(f"No completion row for run {run_id!r}")
        start_state = row["completion_state"]
        if start_state in _TERMINAL:
            return AdvanceResult(run_id, start_state, start_state, row["last_reason_code"], changed=False)

        steps = 0
        while steps < _MAX_STEPS_PER_ADVANCE:
            steps += 1
            state = row["completion_state"]
            if state in _TERMINAL:
                break
            new_row, progressed = self._step(row, now=now)
            row = new_row
            if not progressed:
                break

        return AdvanceResult(
            run_id,
            start_state,
            row["completion_state"],
            row["last_reason_code"],
            changed=row["completion_state"] != start_state,
        )

    def advance_pending(
        self, *, now: datetime | None = None, limit: int = 50
    ) -> list[AdvanceResult]:
        """Advance every due, non-terminal completion row. This is the bounded
        poll the Supervisor/UI tick drives. Never raises for a single row's
        failure — it records REQUIRES_ATTENTION and moves on."""
        now = now or _now()
        due = runtime_db.list_completions(
            self.db_path, states=sorted(_ACTIVE), due_before=_iso(now), limit=limit
        )
        results: list[AdvanceResult] = []
        for row in due:
            try:
                results.append(self.advance(row["run_id"], now=now))
            except runtime_db.LostUpdateError:
                # Benign concurrent progress: another advancer (a second
                # autopilot tick, an on-demand `advance_completions` call) won
                # the compare-and-set race and already moved this row forward.
                # This is NOT a failure — the winning actor's transition is
                # persisted and correct. Skip without terminalizing the row and
                # without bumping any retry/recovery counter; the row (if still
                # non-terminal) is simply picked up on the next due poll.
                continue
            except runtime_db.InvalidCompletionTransitionError as exc:
                # Defense in depth. With CAS-before-guard ordering in
                # `db.update_completion`, a stale advancer now loses with
                # `LostUpdateError`, so reaching here means the transition was
                # illegal at the *current* row version. Re-read to separate a
                # benign race (another worker already terminalized or removed the
                # row — its result stands) from a genuine state-machine error,
                # and never abort the batch either way.
                fresh = runtime_db.get_completion(self.db_path, row["run_id"])
                if fresh is None or fresh["completion_state"] in _TERMINAL:
                    continue
                self._mark_attention(
                    fresh, now=now, reason_code=ReasonCode.RETRY_LIMIT_REACHED,
                    recommended=f"Illegal completion transition: {exc}",
                )
            except (GitHubError, git_ops.GitOpsError) as exc:
                # Transient infrastructure failure (GitHub/network/git remote):
                # schedule a backoff retry rather than giving up. Repeated
                # failures eventually escalate to REQUIRES_ATTENTION.
                self._schedule_retry(
                    row["run_id"], now=now, reason_code=ReasonCode.RETRY_LIMIT_REACHED,
                    message=f"Transient failure, will retry: {exc}",
                )
            except Exception as exc:  # noqa: BLE001 - isolate one row's failure
                fresh = runtime_db.get_completion(self.db_path, row["run_id"])
                if fresh is None:
                    # The completion row genuinely disappeared between listing
                    # and advancing — its run/task was deleted and the row
                    # cascaded away (legitimate concurrent cleanup). There is
                    # nothing left to escalate: skip. (This also guards
                    # `_mark_attention` itself, whose CAS would otherwise raise
                    # a fresh KeyError against the now-missing row.)
                    continue
                self._mark_attention(
                    fresh,
                    now=now,
                    reason_code=ReasonCode.RETRY_LIMIT_REACHED,
                    recommended=f"Completion advance failed: {exc}",
                )
        return results

    def _schedule_retry(self, run_id: str, *, now: datetime, reason_code: str, message: str) -> None:
        """Keep the current state but back off and re-check later. Escalates to
        REQUIRES_ATTENTION once the policy retry limit is reached, so a genuinely
        permanent failure does not retry forever."""
        row = runtime_db.get_completion(self.db_path, run_id)
        if row is None or row["completion_state"] in _TERMINAL:
            return
        policy = CompletionPolicy.from_json(row.get("policy_json"))
        retry_count = (row.get("retry_count") or 0) + 1
        if retry_count >= policy.max_retries:
            self._mark_attention(
                row, now=now, reason_code=reason_code,
                recommended=f"{message} (retry limit reached).",
            )
            return
        runtime_db.append_completion_event(
            self.db_path, run_id, EV_RETRY_SCHEDULED, reason_code=reason_code, message=message
        )
        self._transition(
            row,
            state=row["completion_state"],
            reason_code=reason_code,
            recommended=message,
            now=now,
            retry_count=retry_count,
            next_retry_at=_compute_next_retry(now, retry_count),
            progressed=False,
        )

    # -- single step --------------------------------------------------------------
    def _step(self, row: dict, *, now: datetime) -> tuple[dict, bool]:
        state = row["completion_state"]
        policy = CompletionPolicy.from_json(row.get("policy_json"))

        # --- validation phase ---------------------------------------------------
        if state in (CompletionState.EXECUTION_FINISHED, CompletionState.VALIDATING_RESULT):
            return self._step_validation(row, policy=policy, now=now)

        # --- verification phase (fetch + reachability) --------------------------
        if state in (CompletionState.MERGED, CompletionState.VERIFYING_TARGET_BRANCH):
            return self._step_verify(row, policy=policy, now=now)

        # --- PR phase (result valid / preparing / open / awaiting / blocked /
        #     closed-unmerged / recovery) ---------------------------------------
        return self._step_pull_request(row, policy=policy, now=now)

    def _step_validation(self, row: dict, *, policy: CompletionPolicy, now: datetime) -> tuple[dict, bool]:
        repo = row["repository_path"]
        state = repo_state.inspect_repository(
            repo,
            base_branch=row.get("base_branch"),
            branch=row.get("branch"),
            remote=row.get("remote") or DEFAULT_REMOTE,
            check_remote=False,
        )
        run = runtime_db.get_run(self.db_path, row["run_id"])
        task = runtime_db.get_task(self.db_path, row["task_id"])

        # Guard checks first (dirty / no commit / missing worktree / already in
        # target) via the evaluator without validation — it returns before the
        # validation gate.
        pre = self.evaluator.evaluate(task, run, state, None, validation=None, policy=policy)
        if pre.reason_code == ReasonCode.TARGET_VERIFIED:
            return self._complete(row, now=now, pr=self._pr_from_row(row))
        if pre.reason_code in (
            ReasonCode.UNCOMMITTED_CHANGES,
            ReasonCode.NO_TASK_COMMIT,
            ReasonCode.WORKTREE_MISSING,
            ReasonCode.NOT_A_REPOSITORY,
        ):
            self._apply_assessment(row, pre, now=now)
            return runtime_db.get_completion(self.db_path, row["run_id"]), False

        if not policy.validation_required:
            # Skip straight to the PR phase.
            self._transition(
                row,
                state=CompletionState.RESULT_VALID,
                reason_code=ReasonCode.VALIDATION_PASSED,
                recommended="Validation not required; preparing pull request.",
                now=now,
                progressed=True,
            )
            return runtime_db.get_completion(self.db_path, row["run_id"]), True

        # Run the validation plan.
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_VALIDATION_STARTED, reason_code=ReasonCode.VALIDATION_PENDING
        )
        plan = validation.resolve_validation_plan(task=task, project_cfg=None)
        attempt = (row.get("retry_count") or 0) + 1
        outcome = validation.run_validation(repo, plan)
        for result in outcome.results:
            runtime_db.record_validation_result(
                self.db_path,
                row["run_id"],
                attempt=attempt,
                command=result.command,
                exit_code=result.exit_code,
                started_at=result.started_at,
                finished_at=result.finished_at,
                stdout_summary=result.stdout_summary,
                stderr_summary=result.stderr_summary,
            )
        summary = outcome.summary()
        if outcome.passed:
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_VALIDATION_PASSED, reason_code=ReasonCode.VALIDATION_PASSED,
                message=summary,
            )
            self._transition(
                row,
                state=CompletionState.RESULT_VALID,
                reason_code=ReasonCode.VALIDATION_PASSED,
                recommended="Validation passed; preparing pull request.",
                validation_summary=summary,
                now=now,
                progressed=True,
            )
        else:
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_VALIDATION_FAILED, reason_code=ReasonCode.VALIDATION_FAILED,
                message=summary,
            )
            self._transition(
                row,
                state=CompletionState.VALIDATION_FAILED,
                reason_code=ReasonCode.VALIDATION_FAILED,
                recommended="Validation failed: " + summary + ". Fix and re-run the task.",
                requires_human=True,
                validation_summary=summary,
                now=now,
                progressed=False,
            )
        return runtime_db.get_completion(self.db_path, row["run_id"]), outcome.passed

    def _step_pull_request(self, row: dict, *, policy: CompletionPolicy, now: datetime) -> tuple[dict, bool]:
        repo = Path(row["repository_path"])
        branch = row.get("branch")
        base_branch = row.get("base_branch")
        remote = row.get("remote") or DEFAULT_REMOTE
        run = runtime_db.get_run(self.db_path, row["run_id"])
        task = runtime_db.get_task(self.db_path, row["task_id"])

        # Fetch so the base/target refs reflect the real remote — this is how we
        # detect a change already merged into the target (e.g. a manual merge
        # between ticks, or "already in main despite a closed PR").
        repo_state.fetch(repo, remote)
        rstate = repo_state.inspect_repository(
            repo.as_posix(),
            base_branch=base_branch,
            branch=branch,
            remote=remote,
            verify_commit=row.get("merge_commit"),
            check_remote=True,
        )
        # Already in target? (covers a manual merge that happened between ticks.)
        if rstate.commit_in_target:
            return self._complete(row, now=now, pr=self._pr_from_row(row))

        pr = None
        if branch and policy.requires_pull_request:
            pr = self._discover_pr(repo, branch=branch, base=base_branch)

        # A synthetic passed-validation outcome: we only reach this phase after
        # validation already passed (or was not required).
        passed = validation.ValidationOutcome(
            ran=True,
            results=[
                validation.CommandResult("validation", 0, "", "", row.get("validation_summary") or "", "")
            ],
        ) if policy.validation_required else validation.ValidationOutcome(ran=False)

        assessment = self.evaluator.evaluate(
            task, run, rstate, pr, validation=passed, policy=policy, enforce_clean_tree=False
        )
        action = assessment.action

        if action == CompletionAction.OPEN_PULL_REQUEST:
            return self._open_pull_request(row, rstate, policy=policy, now=now, task=task)
        if action == CompletionAction.RECOVER_PULL_REQUEST:
            return self._recover_pull_request(row, rstate, pr, policy=policy, now=now, task=task)
        if action == CompletionAction.MERGE:
            return self._merge(row, pr, policy=policy, now=now)
        if action == CompletionAction.VERIFY_TARGET:
            # PR merged since last tick — jump to verify.
            self._transition(
                row,
                state=CompletionState.MERGED,
                reason_code=ReasonCode.PR_MERGED,
                recommended=assessment.recommended_action,
                merge_commit=assessment.merge_commit,
                pull_request_number=assessment.pull_request_number,
                pull_request_url=assessment.pull_request_url,
                pull_request_state=assessment.pull_request_state,
                now=now,
                progressed=True,
            )
            return runtime_db.get_completion(self.db_path, row["run_id"]), True
        if action == CompletionAction.NONE:
            # Terminal (completed/attention) — persist and stop.
            self._apply_assessment(row, assessment, now=now)
            return runtime_db.get_completion(self.db_path, row["run_id"]), False
        # WAIT — persist the resting state and schedule a backoff re-check.
        self._apply_assessment(row, assessment, now=now, waiting=True)
        return runtime_db.get_completion(self.db_path, row["run_id"]), False

    def _step_verify(self, row: dict, *, policy: CompletionPolicy, now: datetime) -> tuple[dict, bool]:
        repo = Path(row["repository_path"])
        remote = row.get("remote") or DEFAULT_REMOTE
        merge_commit = row.get("merge_commit")
        # Fetch so remote-tracking refs reflect the real merge, then check
        # reachability of head OR the merge commit (supports squash merges).
        repo_state.fetch(repo, remote)
        rstate = repo_state.inspect_repository(
            repo.as_posix(),
            base_branch=row.get("base_branch"),
            branch=row.get("branch"),
            remote=remote,
            verify_commit=merge_commit,
            check_remote=False,
        )
        if rstate.commit_in_target:
            return self._complete(row, now=now, pr=self._pr_from_row(row))
        # Not yet visible — stay verifying with backoff, up to the retry cap.
        retry_count = (row.get("retry_count") or 0) + 1
        if retry_count >= policy.max_retries:
            self._mark_attention(
                row,
                now=now,
                reason_code=ReasonCode.TARGET_NOT_VERIFIED,
                recommended="Merge reported by GitHub is still not present in the target branch after "
                "repeated checks. Manual verification required.",
            )
            return runtime_db.get_completion(self.db_path, row["run_id"]), False
        self._transition(
            row,
            state=CompletionState.VERIFYING_TARGET_BRANCH,
            reason_code=ReasonCode.TARGET_NOT_VERIFIED,
            recommended="Waiting for the merge to become reachable from the target branch.",
            now=now,
            retry_count=retry_count,
            next_retry_at=_compute_next_retry(now, retry_count),
            progressed=False,
        )
        return runtime_db.get_completion(self.db_path, row["run_id"]), False

    # -- actions ------------------------------------------------------------------
    def _open_pull_request(self, row, rstate, *, policy, now, task) -> tuple[dict, bool]:
        repo = Path(row["repository_path"])
        branch = row["branch"]
        base_branch = row["base_branch"]
        remote = row.get("remote") or DEFAULT_REMOTE
        if not branch or branch == base_branch:
            self._mark_attention(
                row,
                now=now,
                reason_code=ReasonCode.NO_PR_NOT_IN_TARGET,
                recommended="Cannot open a pull request: the run's branch equals the base branch or is unknown.",
            )
            return runtime_db.get_completion(self.db_path, row["run_id"]), False
        if not rstate.remote_branch_exists:
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_REMOTE_BRANCH_MISSING, reason_code=ReasonCode.PR_MISSING,
                message=f"Remote branch {branch!r} missing; pushing.",
            )
        git_ops.push_branch(repo, remote=remote, branch=branch)
        # First-time publication of this branch on the remote — not a recovery
        # recreation (that path lives in `_recover_pull_request` and keeps the
        # `REMOTE_BRANCH_RECREATED` label).
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_REMOTE_BRANCH_PUBLISHED, reason_code=ReasonCode.PR_MISSING,
            message=f"Pushed branch {branch!r} to {remote}.",
        )
        title, body = _pr_content(task, row, rstate, base_branch=base_branch, branch=branch)
        pr = self.github.create_pull_request(repo, base=base_branch, head=branch, title=title, body=body)
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_PR_CREATED, reason_code=ReasonCode.PR_OPEN,
            message=f"Opened pull request #{pr.number}.", metadata={"url": pr.url, "number": pr.number},
        )
        self._transition(
            row,
            state=CompletionState.PULL_REQUEST_OPEN,
            reason_code=ReasonCode.PR_OPEN,
            recommended="Pull request opened.",
            pull_request_number=pr.number,
            pull_request_url=pr.url,
            pull_request_state=pr.state,
            remote_branch=branch,
            now=now,
            progressed=True,
        )
        return runtime_db.get_completion(self.db_path, row["run_id"]), True

    def _recover_pull_request(self, row, rstate, pr, *, policy, now, task) -> tuple[dict, bool]:
        repo = Path(row["repository_path"])
        branch = row["branch"]
        base_branch = row["base_branch"]
        remote = row.get("remote") or DEFAULT_REMOTE
        closed_number = pr.number if pr is not None else row.get("pull_request_number")
        closed_url = pr.url if pr is not None else row.get("pull_request_url")

        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_PR_CLOSED_UNMERGED, reason_code=ReasonCode.PR_CLOSED_UNMERGED,
            message=f"Pull request #{closed_number} closed without merging.",
            metadata={"closed_pr": closed_number},
        )
        recovery_count = (row.get("recovery_count") or 0) + 1
        try:
            # Recreate the remote branch (idempotent push) and open a
            # replacement PR. Discovery inside create prevents duplicates on
            # restart: if a replacement already exists, the evaluator would have
            # seen an OPEN PR and never reached this recovery path.
            if not rstate.remote_branch_exists:
                git_ops.push_branch(repo, remote=remote, branch=branch)
                runtime_db.append_completion_event(
                    self.db_path, row["run_id"], EV_REMOTE_BRANCH_RECREATED,
                    reason_code=ReasonCode.RECOVERABLE, message=f"Recreated remote branch {branch!r}.",
                )
            existing = self._discover_pr(repo, branch=branch, base=base_branch)
            if existing is not None and existing.is_open:
                replacement = existing
            else:
                title, body = _pr_content(
                    task, row, rstate, base_branch=base_branch, branch=branch, replaced_pr=closed_number
                )
                replacement = self.github.create_pull_request(
                    repo, base=base_branch, head=branch, title=title, body=body
                )
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_REPLACEMENT_PR_CREATED, reason_code=ReasonCode.PR_OPEN,
                message=f"Replacement pull request #{replacement.number} for closed #{closed_number}.",
                metadata={"replacement_pr": replacement.number, "replaced_pr": closed_number},
            )
        except Exception as exc:  # noqa: BLE001
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_RECOVERY_FAILED, reason_code=ReasonCode.RECOVERY_NOT_POSSIBLE,
                message=str(exc),
            )
            self._transition(
                row,
                state=CompletionState.RECOVERY_FAILED,
                reason_code=ReasonCode.RECOVERY_NOT_POSSIBLE,
                recommended=f"Automatic recovery failed: {exc}. Manual recovery required.",
                requires_human=True,
                recovery_count=recovery_count,
                now=now,
                progressed=False,
            )
            return runtime_db.get_completion(self.db_path, row["run_id"]), False

        self._transition(
            row,
            state=CompletionState.PULL_REQUEST_OPEN,
            reason_code=ReasonCode.PR_OPEN,
            recommended="Replacement pull request opened after closed-unmerged recovery.",
            pull_request_number=replacement.number,
            pull_request_url=replacement.url,
            pull_request_state=replacement.state,
            replaced_pull_request_number=closed_number,
            replaced_pull_request_url=closed_url,
            remote_branch=branch,
            recovery_count=recovery_count,
            now=now,
            progressed=True,
        )
        return runtime_db.get_completion(self.db_path, row["run_id"]), True

    def _merge(self, row, pr, *, policy, now) -> tuple[dict, bool]:
        repo = Path(row["repository_path"])
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_MERGE_STARTED, reason_code=ReasonCode.READY_TO_MERGE,
            message=f"Merging pull request #{pr.number} via {policy.merge_method}.",
        )
        merged = self.github.merge_pull_request(repo, number=pr.number, method=policy.merge_method)
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_PR_MERGED, reason_code=ReasonCode.PR_MERGED,
            message=f"Merged pull request #{pr.number}.", metadata={"merge_commit": merged.merge_commit},
        )
        self._transition(
            row,
            state=CompletionState.MERGED,
            reason_code=ReasonCode.PR_MERGED,
            recommended="Pull request merged; verifying target branch.",
            merge_commit=merged.merge_commit,
            pull_request_state=merged.state,
            now=now,
            progressed=True,
        )
        return runtime_db.get_completion(self.db_path, row["run_id"]), True

    def _complete(self, row, *, now, pr) -> tuple[dict, bool]:
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_TARGET_BRANCH_VERIFIED, reason_code=ReasonCode.TARGET_VERIFIED,
            message="Merge is reachable from the target branch.",
        )
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_TASK_COMPLETED, reason_code=ReasonCode.TARGET_VERIFIED,
            message="Task completed and present in the target branch.",
        )
        self._transition(
            row,
            state=CompletionState.COMPLETED,
            reason_code=ReasonCode.TARGET_VERIFIED,
            recommended="Task completed and merged into the target branch.",
            now=now,
            progressed=False,
        )
        return runtime_db.get_completion(self.db_path, row["run_id"]), False

    # -- persistence helpers ------------------------------------------------------
    def _discover_pr(self, repo: Path, *, branch: str, base: str | None) -> PullRequestState | None:
        return self.github.discover_pull_request(repo, branch=branch, base=base)

    def _pr_from_row(self, row: dict) -> PullRequestState | None:
        if not row.get("pull_request_number"):
            return None
        return PullRequestState(
            number=row.get("pull_request_number"),
            url=row.get("pull_request_url"),
            state=row.get("pull_request_state"),
            merge_commit=row.get("merge_commit"),
        )

    def _apply_assessment(
        self, row: dict, assessment: CompletionAssessment, *, now: datetime, waiting: bool = False
    ) -> None:
        retry_count = (row.get("retry_count") or 0)
        next_retry = None
        if waiting:
            retry_count += 1
            next_retry = _compute_next_retry(now, retry_count)
        # Persist first, then log. If a concurrent winner advanced the row the
        # CAS in `_transition` raises before we append, so a benign loser never
        # records a misleading REQUIRES_ATTENTION audit event (AICC-AUTONOMY-002).
        self._transition(
            row,
            state=assessment.status,
            reason_code=assessment.reason_code,
            recommended=assessment.recommended_action,
            requires_human=assessment.requires_human,
            is_recoverable=assessment.is_recoverable,
            pull_request_number=assessment.pull_request_number,
            pull_request_url=assessment.pull_request_url,
            pull_request_state=assessment.pull_request_state,
            merge_commit=assessment.merge_commit,
            now=now,
            retry_count=retry_count if waiting else None,
            next_retry_at=next_retry,
            progressed=False,
        )
        if assessment.status == CompletionState.REQUIRES_ATTENTION:
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_REQUIRES_ATTENTION, reason_code=assessment.reason_code,
                message=assessment.recommended_action,
            )

    def _mark_attention(self, row: dict, *, now: datetime, reason_code: str, recommended: str) -> None:
        """Escalate a row to REQUIRES_ATTENTION — idempotent and race-safe.

        A concurrency loser must never overwrite or terminalize the winner, so
        this re-reads the row and refuses to act when another worker has already
        moved on: a missing row (deleted/cascaded), an already-terminal row, or
        a row whose CAS is lost between re-read and write are all left untouched
        — no state change, no `requires_human`/retry mutation, and no audit
        event. The REQUIRES_ATTENTION audit event is appended only *after* the
        transition commits, so the log never claims a change that did not happen
        (AICC-AUTONOMY-002)."""
        fresh = runtime_db.get_completion(self.db_path, row["run_id"])
        if fresh is None or fresh["completion_state"] in _TERMINAL:
            # Row was deleted, or another worker already reached a terminal
            # state (possibly a legitimate winner). Nothing to escalate.
            return
        try:
            self._transition(
                fresh,
                state=CompletionState.REQUIRES_ATTENTION,
                reason_code=reason_code,
                recommended=recommended,
                requires_human=True,
                now=now,
                progressed=False,
            )
        except (runtime_db.LostUpdateError, runtime_db.InvalidCompletionTransitionError):
            # Another worker advanced or terminalized the row between our
            # re-read and the compare-and-set. Its progress is authoritative;
            # do not clobber it and do not raise (which would abort the batch).
            return
        runtime_db.append_completion_event(
            self.db_path, row["run_id"], EV_REQUIRES_ATTENTION, reason_code=reason_code, message=recommended
        )

    def _transition(
        self,
        row: dict,
        *,
        state: str,
        reason_code: str,
        recommended: str,
        now: datetime,
        progressed: bool,
        requires_human: bool = False,
        is_recoverable: bool = False,
        validation_summary: str | None = None,
        pull_request_number: int | None = None,
        pull_request_url: str | None = None,
        pull_request_state: str | None = None,
        replaced_pull_request_number: int | None = None,
        replaced_pull_request_url: str | None = None,
        merge_commit: str | None = None,
        remote_branch: str | None = None,
        retry_count: int | None = None,
        recovery_count: int | None = None,
        next_retry_at: str | None = "__unset__",
    ) -> dict:
        """Persist a completion transition via compare-and-set. On a progressed
        transition the retry counter resets (next phase should be checked
        promptly); on a waiting transition the caller passes an explicit
        `retry_count`/`next_retry_at` backoff."""
        fields: dict[str, object] = {
            "completion_state": state,
            "last_reason_code": reason_code,
            "recommended_action": recommended,
            "requires_human": 1 if requires_human else 0,
            "is_recoverable": 1 if is_recoverable else 0,
            "last_checked_at": _iso(now),
        }
        if progressed:
            fields["retry_count"] = 0
            fields["next_retry_at"] = _iso(now)
        if retry_count is not None:
            fields["retry_count"] = retry_count
        if next_retry_at != "__unset__":
            fields["next_retry_at"] = next_retry_at
        if validation_summary is not None:
            fields["validation_summary"] = validation_summary
        if pull_request_number is not None:
            fields["pull_request_number"] = pull_request_number
        if pull_request_url is not None:
            fields["pull_request_url"] = pull_request_url
        if pull_request_state is not None:
            fields["pull_request_state"] = pull_request_state
        if replaced_pull_request_number is not None:
            fields["replaced_pull_request_number"] = replaced_pull_request_number
        if replaced_pull_request_url is not None:
            fields["replaced_pull_request_url"] = replaced_pull_request_url
        if merge_commit is not None:
            fields["merge_commit"] = merge_commit
        if remote_branch is not None:
            fields["remote_branch"] = remote_branch
        if recovery_count is not None:
            fields["recovery_count"] = recovery_count
        updated = runtime_db.update_completion(
            self.db_path, row["run_id"], expected_version=row["version"], fields=fields
        )
        if row["completion_state"] != state:
            runtime_db.append_completion_event(
                self.db_path, row["run_id"], EV_STATE_CHANGED, reason_code=reason_code,
                message=f"{row['completion_state']} -> {state}",
            )
        return updated


_TERMINAL = frozenset(
    {
        CompletionState.COMPLETED,
        CompletionState.VALIDATION_FAILED,
        CompletionState.REQUIRES_ATTENTION,
        CompletionState.RECOVERY_FAILED,
    }
)
_ACTIVE = frozenset(
    {
        CompletionState.EXECUTION_FINISHED,
        CompletionState.VALIDATING_RESULT,
        CompletionState.RESULT_VALID,
        CompletionState.PREPARING_PULL_REQUEST,
        CompletionState.PULL_REQUEST_OPEN,
        CompletionState.AWAITING_MERGE,
        CompletionState.MERGED,
        CompletionState.VERIFYING_TARGET_BRANCH,
        CompletionState.PR_CLOSED_UNMERGED,
        CompletionState.MERGE_BLOCKED,
        CompletionState.RECOVERY_PENDING,
    }
)


def _pr_content(task, row, rstate, *, base_branch, branch, replaced_pr=None) -> tuple[str, str]:
    """Build a PR title and body (safe plain text — never shell)."""
    title = (task or {}).get("title") or f"Task {row['task_id']}"
    lines = [
        f"## Task {row['task_id']}",
        "",
        f"**Title:** {title}",
        f"**Source branch:** `{branch}`",
        f"**Target branch:** `{base_branch}`",
        f"**Head commit:** `{rstate.head_commit or row.get('head_commit') or '-'}`",
        "",
        "### Validation",
        row.get("validation_summary") or "See completion validation records.",
        "",
        "_Opened automatically by the AI Command Center autonomous completion pipeline._",
    ]
    if replaced_pr:
        lines.insert(2, f"**Replaces closed pull request:** #{replaced_pr}")
    return f"{title} (task {row['task_id']})", "\n".join(lines)
