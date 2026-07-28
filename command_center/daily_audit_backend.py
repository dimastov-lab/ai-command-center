"""Production backend for daily self-audit campaigns.

This adapter intentionally reuses the existing Execution Center.  It creates a
single, strongly-instructed engineering run whose completion policy performs
validation, PR creation, CI/review gating, merge, and target verification.
The run itself cannot commit, push, or merge because Supervisor applies the
trusted-development disallow list; those privileged actions remain owned by
the completion pipeline.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from command_center import models, report_parser
from command_center.daily_audit import CampaignRequest, CampaignResult
from command_center.runtime import reports
from command_center.runtime.api import ExecutionCenterAPI
from command_center.runtime.completion_service import CompletionOrchestrator


_PROMPT = """Perform the scheduled deep product and engineering self-audit of
AI Command Center. Treat the real user journey as a release-critical contract,
not as a visual spot check.

Required workflow:
1. Audit implementation, architecture, security, reliability, concurrency,
   tests, CI configuration, packaging and documentation.
2. Exercise the complete founder/user journey with real application behavior:
   create/import a task, validate/select its workspace, launch it, observe live
   progress, handle review/remediation, run validations, create and gate a PR,
   merge it, and verify the target branch and final task state. Use browser/UI
   automation where the product surface is interactive. Record screenshots or
   equivalent evidence for material UX findings.
3. Evaluate usability and quality at every step: clarity of labels and status,
   discoverability, feedback, latency, error messages, recovery actions,
   accessibility, misleading success states, and places where a user can get
   stuck or lose work.
4. Exercise negative paths: invalid workspace, dirty tree, concurrent launch,
   agent timeout/crash, malformed report, failed tests, GitHub/network failure,
   review rejection, merge conflict, restart during work, and retry recovery.
   Confirm failures remain visible and recoverable and are never reported as
   success.
5. Inspect automated remediation end to end. Confirm a reproduced defect turns
   into a bounded repair attempt, regression coverage, independent re-review,
   green validation and verified merge. Never loop forever or hide an
   unresolved finding.
6. Inspect queue waves and dependency gates using realistic mixed task sets:
   ordering, capacity, exclusivity per workspace, blocked dependencies,
   starvation/fairness, isolated item failure, restart idempotency and the
   transition from one wave to the next.
7. Reassess task priority from current evidence. Prioritize safety/data-loss
   and blocked-user-path defects first, then reliability, correctness, UX and
   maintainability. Do not reorder merely by intuition; document the evidence
   and dependency impact behind every changed priority.
8. Reproduce every actionable finding and fix confirmed defects only. Add a
   regression test for each corrected defect and focused checks after repair.
9. Repeat audit, remediation and independent review until no
   Blocker/High/Medium defect remains and the full user journey passes.
10. Run the complete configured validation suite, CI-equivalent checks and a
    final release review.
11. Finish only with APPROVED FOR COMMIT, a clean reviewable diff, an explicit
    user-journey result, queue-wave result, unresolved-risk list, and exact
    evidence for every test actually run.

Do not commit, push, create/merge a PR, reset, stash, rebase, or weaken tests.
The Command Center completion pipeline exclusively owns publication and merge.
If a safe fix cannot be produced, return NOT APPROVED FOR COMMIT with exact
evidence; never conceal or downgrade a failure.
"""


class ExecutionCenterCampaignBackend:
    def __init__(self, api: ExecutionCenterAPI | None = None, *, poll_seconds: float = 5.0) -> None:
        self.api = api or ExecutionCenterAPI()
        self.poll_seconds = poll_seconds

    def _git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()

    def _prepare_worktree(self, request: CampaignRequest) -> tuple[Path, str]:
        repository = request.repository_path.resolve()
        if self._git(repository, "status", "--porcelain"):
            # The scheduler source checkout may contain development work, but
            # the campaign never runs there. Fetching is safe; all mutation is
            # isolated in a new linked worktree based on live origin/main.
            pass
        self._git(repository, "fetch", "origin", "main")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        branch = f"codex/daily-audit-{stamp}-{request.campaign_id[:8]}"
        worktrees_root = repository.parent / f".{repository.name}-daily-audit-worktrees"
        worktrees_root.mkdir(parents=True, exist_ok=True)
        worktree = worktrees_root / request.campaign_id
        self._git(
            repository,
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree),
            "origin/main",
        )
        return worktree, branch

    def _wait_run(self, run_id: str) -> dict:
        while True:
            current = self.api.get_run(run_id)
            if current is None:
                raise RuntimeError(f"Run disappeared: {run_id}")
            if current["state"] in {"FAILED", "TIMED_OUT", "CANCELLED", "STALE"}:
                raise RuntimeError(f"Agent run ended in {current['state']}.")
            if current["state"] == "COMPLETED":
                return current
            time.sleep(self.poll_seconds)

    def _launch(
        self,
        *,
        request: CampaignRequest,
        worktree: Path,
        branch: str,
        task_type: str,
        instruction: str,
        task_id: str | None = None,
    ) -> tuple[dict, dict]:
        run = self.api.start_run(
            project="AICC",
            repository_path=str(worktree),
            task_type=task_type,
            instruction=instruction,
            confirmed=True,
            title=f"Daily self-audit {request.campaign_id[:8]}: {task_type}",
            launch_source="daily_self_audit",
            task_id=task_id,
            metadata={
                "campaign_id": request.campaign_id,
                "max_remediation_rounds": request.max_remediation_rounds,
            },
            timeout_seconds=None,
            expected_branch=branch,
            repository_already_validated=True,
        )
        current = self._wait_run(run["id"])
        events = self.api.get_events(run["id"])
        parsed = report_parser.parse_report(reports.result_text(events))
        return current, parsed

    def _validate(self, request: CampaignRequest, worktree: Path) -> None:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for command in request.validation_commands:
            result = subprocess.run(
                list(command),
                cwd=worktree,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout)[-4000:]
                raise RuntimeError(f"validation failed: {' '.join(command)}\n{detail}")

    def _commit(self, worktree: Path, campaign_id: str) -> str:
        if not self._git(worktree, "status", "--porcelain"):
            raise RuntimeError("Audit produced no change to publish.")
        self._git(worktree, "add", "--all")
        self._git(
            worktree,
            "commit",
            "-m",
            f"Daily AI Command Center audit {campaign_id[:8]}",
        )
        return self._git(worktree, "rev-parse", "HEAD")

    def _cleanup_worktree(self, repository: Path, worktree: Path) -> None:
        if worktree.exists() and not self._git(worktree, "status", "--porcelain"):
            self._git(repository, "worktree", "remove", str(worktree))
        parent = worktree.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def run(self, request: CampaignRequest) -> CampaignResult:
        worktree, branch = self._prepare_worktree(request)
        runtime_task = None
        try:
            implementation, parsed = self._launch(
                request=request,
                worktree=worktree,
                branch=branch,
                task_type="implementation",
                instruction=_PROMPT,
            )
            runtime_task = implementation["task_id"]
            gate_prompt = (
                "Independently perform the final release gate for the scheduled "
                "daily self-audit. Verify the complete diff, full user journey, "
                "queue-wave behavior, regression tests and all claims. Return "
                "exactly APPROVED FOR COMMIT only when no Blocker/High/Medium "
                "finding remains; otherwise return NOT APPROVED FOR COMMIT with "
                "actionable evidence."
            )
            approved = False
            last_gate = parsed
            final_run = implementation
            for round_number in range(request.max_remediation_rounds):
                final_run, last_gate = self._launch(
                    request=request,
                    worktree=worktree,
                    branch=branch,
                    task_type="final_gate",
                    instruction=gate_prompt,
                    task_id=runtime_task,
                )
                approved = (
                    last_gate.get("verdict") == models.VERDICT_APPROVED_FOR_COMMIT
                    and not last_gate.get("verdict_contradictory")
                )
                if approved:
                    try:
                        self._validate(request, worktree)
                        break
                    except RuntimeError as validation_error:
                        approved = False
                        findings = {"Validation": [str(validation_error)]}
                else:
                    findings = last_gate.get("findings") or {}
                if round_number + 1 >= request.max_remediation_rounds:
                    break
                remediation_prompt = (
                    "Remediate only the independently confirmed daily-audit "
                    "findings below. Add regression tests and run focused plus "
                    "full validation. Do not commit or publish.\n\n"
                    + str(findings)
                )
                self._launch(
                    request=request,
                    worktree=worktree,
                    branch=branch,
                    task_type="remediation",
                    instruction=remediation_prompt,
                    task_id=runtime_task,
                )
            if not approved:
                return CampaignResult(
                    status="requires_attention",
                    summary="Independent final gate did not approve within the bounded remediation rounds.",
                )

            if not self._git(worktree, "status", "--porcelain"):
                self._cleanup_worktree(request.repository_path.resolve(), worktree)
                return CampaignResult(
                    status="completed",
                    summary="Daily audit and final gate passed; no repository changes were required.",
                    target_verified=True,
                )
            head = self._commit(worktree, request.campaign_id)
            runtime_cfg = {
                "default_branch": "main",
                "merge_mode": request.merge_mode,
                "merge_method": "squash",
                "requires_pull_request": True,
                "allow_local_only": False,
                "allow_pr_recovery": True,
            }
            policy_task = {
                "id": runtime_task,
                "merge_mode": request.merge_mode,
                "merge_method": "squash",
            }
            CompletionOrchestrator(self.api.db_path).begin_completion(
                {**final_run, "commit_hash": head},
                task=policy_task,
                project_cfg=runtime_cfg,
            )
        except Exception:
            # Preserve a dirty or failed worktree for forensic inspection.
            raise

        while True:
            completion = self.api.get_completion(final_run["id"])
            if completion is None:
                raise RuntimeError("Completion row was not seeded.")
            state = completion["completion_state"]
            if state == "COMPLETED":
                self._cleanup_worktree(request.repository_path.resolve(), worktree)
                return CampaignResult(
                    status="completed",
                    summary="Audit remediation merged and target branch verified.",
                    target_verified=True,
                    pull_request_url=completion.get("pull_request_url"),
                )
            if state in {"VALIDATION_FAILED", "REQUIRES_ATTENTION", "RECOVERY_FAILED"}:
                return CampaignResult(
                    status="requires_attention",
                    summary=f"Completion stopped in {state}: {completion.get('last_reason_code')}",
                    pull_request_url=completion.get("pull_request_url"),
                )
            self.api.advance_completions(limit=10)
            time.sleep(self.poll_seconds)
