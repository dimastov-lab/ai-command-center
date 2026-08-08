import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from command_center.daily_audit import CampaignBackendError, CampaignRequest
from command_center.daily_audit_backend import (
    ChangeEntry,
    ExecutionCenterCampaignBackend,
    GateDecision,
    UnsafeCampaignChanges,
)
from command_center import daily_audit_backend as daily_audit_backend_module


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def repository_with_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(repo))
    git(repo, "config", "user.email", "audit@example.test")
    git(repo, "config", "user.name", "Daily Audit Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return repo


def request(repository_path, **overrides):
    values = {
        "campaign_id": "a" * 32,
        "repository_path": repository_path,
        "max_remediation_rounds": 1,
        "merge_mode": "auto_after_checks",
        "validation_commands": (),
        "run_timeout_seconds": 30,
        "git_timeout_seconds": 10,
        "validation_timeout_seconds": 10,
        "completion_timeout_seconds": 30,
    }
    values.update(overrides)
    return CampaignRequest(**values)


class EventAPI:
    def __init__(self, *, state="COMPLETED", events=None, failure_reason=None):
        self.state = state
        self.events = events or []
        self.failure_reason = failure_reason
        self.cancelled = []

    def get_run(self, run_id):
        return {
            "id": run_id,
            "state": self.state,
            "failure_reason": self.failure_reason,
        }

    def get_events(self, run_id, *, after_seq=0, limit=1000):
        return [event for event in self.events if event["seq"] > after_seq][:limit]

    def request_cancel(self, run_id, *, confirmed, grace_seconds=None):
        self.cancelled.append((run_id, confirmed, grace_seconds))
        return self.get_run(run_id)


def test_terminal_result_is_loaded_after_more_than_one_thousand_events():
    events = [
        {"seq": seq, "event_type": "assistant_message", "payload": {}}
        for seq in range(1, 1_205)
    ]
    events.append(
        {
            "seq": 1_205,
            "event_type": "result",
            "payload": {"result": "APPROVED FOR COMMIT"},
        }
    )
    backend = ExecutionCenterCampaignBackend(api=EventAPI(events=events), poll_seconds=0)
    loaded = backend._load_all_events("run-1")
    assert len(loaded) == 1_205
    assert backend._result_text(loaded) == "APPROVED FOR COMMIT"


@pytest.mark.parametrize("state", ["INTERRUPTED", "UNKNOWN"])
def test_wait_run_treats_all_runtime_terminal_states_as_terminal(state):
    api = EventAPI(
        state=state,
        failure_reason="orphaned",
        events=[{"seq": 1, "event_type": "result", "payload": {"result": "lost owner"}}],
    )
    backend = ExecutionCenterCampaignBackend(api=api, poll_seconds=0)
    with pytest.raises(CampaignBackendError, match=f"{state}.*orphaned.*lost owner"):
        backend._wait_run("run-1", timeout_seconds=1)


def test_wait_run_has_a_bounded_deadline_and_requests_cancellation():
    ticks = iter([0.0, 2.0])
    api = EventAPI(state="RUNNING")
    backend = ExecutionCenterCampaignBackend(
        api=api,
        poll_seconds=0,
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(CampaignBackendError, match="deadline"):
        backend._wait_run("run-1", timeout_seconds=1)
    assert api.cancelled and api.cancelled[0][0] == "run-1"


def test_git_retries_only_transient_transport_errors(tmp_path, monkeypatch):
    results = iter(
        [
            subprocess.CompletedProcess([], 1, "", "Could not resolve host: github.com"),
            subprocess.CompletedProcess([], 1, "", "SSL connection timeout"),
            subprocess.CompletedProcess([], 0, "ok\n", ""),
        ]
    )
    calls = []
    sleeps = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return next(results)

    monkeypatch.setattr(daily_audit_backend_module.subprocess, "run", fake_run)
    backend = ExecutionCenterCampaignBackend(sleep=sleeps.append)
    assert backend._git(
        tmp_path, "fetch", "origin", retry_attempts=3, retry_base_seconds=2
    ) == "ok"
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_git_permanent_error_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess([], 128, "", "fatal: bad refspec")

    monkeypatch.setattr(daily_audit_backend_module.subprocess, "run", fake_run)
    backend = ExecutionCenterCampaignBackend(sleep=lambda _seconds: None)
    with pytest.raises(CampaignBackendError, match="bad refspec"):
        backend._git(tmp_path, "fetch", retry_attempts=3)
    assert len(calls) == 1


def test_git_transport_retry_is_strictly_bounded(tmp_path, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess([], 1, "", "network is unreachable")

    monkeypatch.setattr(daily_audit_backend_module.subprocess, "run", fake_run)
    backend = ExecutionCenterCampaignBackend(sleep=lambda _seconds: None)
    with pytest.raises(CampaignBackendError, match="exhausted after 3 attempt"):
        backend._git(tmp_path, "fetch", retry_attempts=3)
    assert len(calls) == 3


def test_wait_run_aborts_and_cancels_immediately_after_lease_loss(tmp_path):
    abort_event = threading.Event()
    abort_event.set()
    api = EventAPI(state="RUNNING")
    backend = ExecutionCenterCampaignBackend(api=api, poll_seconds=0)
    campaign_request = request(tmp_path, abort_event=abort_event)

    with pytest.raises(CampaignBackendError, match="losing its scheduler lease"):
        backend._wait_run("run-fenced", timeout_seconds=30, request=campaign_request)

    assert api.cancelled == [("run-fenced", True, 1.0)]


def test_validation_abort_terminates_its_process_group(tmp_path, monkeypatch):
    abort_event = threading.Event()
    captured = []
    original_popen = subprocess.Popen

    def capturing_popen(*args, **kwargs):
        assert kwargs.get("start_new_session") is True
        process = original_popen(*args, **kwargs)
        captured.append(process)
        return process

    monkeypatch.setattr(daily_audit_backend_module.subprocess, "Popen", capturing_popen)
    backend = ExecutionCenterCampaignBackend(api=EventAPI(), poll_seconds=0.01)
    campaign_request = request(
        tmp_path,
        validation_commands=(
            (
                sys.executable,
                "-c",
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                "time.sleep(30)",
            ),
        ),
        validation_timeout_seconds=30,
        abort_event=abort_event,
    )
    errors = []

    def validate():
        try:
            backend._validate(campaign_request, tmp_path)
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    worker = threading.Thread(target=validate)
    worker.start()
    deadline = backend.monotonic() + 2
    while not captured:
        assert backend.monotonic() < deadline
        backend.sleep(0.01)
    abort_event.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CampaignBackendError)
    assert "losing its scheduler lease" in str(errors[0])
    assert captured[0].poll() is not None


def test_provider_429_preserves_message_and_reset_time():
    reset_at = 1_800_000_000
    api = EventAPI(
        state="FAILED",
        failure_reason="provider_api_error",
        events=[
            {
                "seq": 1,
                "event_type": "unknown_type",
                "payload": {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "rejected", "resetsAt": reset_at},
                },
            },
            {
                "seq": 2,
                "event_type": "result",
                "payload": {
                    "api_error_status": 429,
                    "result": "weekly limit reached",
                },
            },
        ],
    )
    backend = ExecutionCenterCampaignBackend(api=api, poll_seconds=0)
    with pytest.raises(CampaignBackendError) as caught:
        backend._wait_run("run-429", timeout_seconds=1)
    assert "provider_api_error" in str(caught.value)
    assert "429" in str(caught.value)
    assert "weekly limit reached" in str(caught.value)
    assert caught.value.retry_at == datetime.fromtimestamp(reset_at, tz=timezone.utc)


def gate_text(
    manifest_digest,
    validation_digest,
    campaign_evidence_digest,
    *,
    high=None,
    evidence=None,
    findings=None,
):
    contract = {
        "approved": True,
        "verdict": "APPROVED FOR COMMIT",
        "findings": (
            findings
            if findings is not None
            else {"Blocker": [], "High": high or [], "Medium": [], "Low": []}
        ),
        "evidence": (
            evidence
            if evidence is not None
            else {
                "diff_review": "Reviewed every manifest entry.",
                "validation": "All supplied checks passed.",
                "user_journey": "User journey evidence reviewed.",
                "queue_waves": "Queue-wave evidence reviewed.",
            }
        ),
        "manifest_sha256": manifest_digest,
        "validation_sha256": validation_digest,
        "campaign_evidence_sha256": campaign_evidence_digest,
    }
    return "DAILY_AUDIT_GATE_JSON: " + json.dumps(contract, separators=(",", ":"))


def test_gate_contract_fails_closed_on_findings_missing_evidence_or_digest_mismatch():
    backend = ExecutionCenterCampaignBackend(api=EventAPI(), poll_seconds=0)
    manifest_digest = "a" * 64
    validation_digest = "b" * 64
    campaign_evidence_digest = "c" * 64
    approved = backend._assess_gate(
        gate_text(manifest_digest, validation_digest, campaign_evidence_digest),
        expected_manifest_digest=manifest_digest,
        expected_validation_digest=validation_digest,
        expected_campaign_evidence_digest=campaign_evidence_digest,
    )
    assert approved.approved

    high = backend._assess_gate(
        gate_text(
            manifest_digest,
            validation_digest,
            campaign_evidence_digest,
            high=["race"],
        ),
        expected_manifest_digest=manifest_digest,
        expected_validation_digest=validation_digest,
        expected_campaign_evidence_digest=campaign_evidence_digest,
    )
    assert not high.approved
    assert high.findings["High"] == ["race"]

    missing = backend._assess_gate(
        gate_text(
            manifest_digest,
            validation_digest,
            campaign_evidence_digest,
            evidence={},
        ),
        expected_manifest_digest=manifest_digest,
        expected_validation_digest=validation_digest,
        expected_campaign_evidence_digest=campaign_evidence_digest,
    )
    assert not missing.approved

    missing_severities = backend._assess_gate(
        gate_text(
            manifest_digest,
            validation_digest,
            campaign_evidence_digest,
            findings={},
        ),
        expected_manifest_digest=manifest_digest,
        expected_validation_digest=validation_digest,
        expected_campaign_evidence_digest=campaign_evidence_digest,
    )
    assert not missing_severities.approved

    mismatch = backend._assess_gate(
        gate_text("d" * 64, validation_digest, campaign_evidence_digest),
        expected_manifest_digest=manifest_digest,
        expected_validation_digest=validation_digest,
        expected_campaign_evidence_digest=campaign_evidence_digest,
    )
    assert not mismatch.approved


def test_change_manifest_rejects_runtime_outputs_and_stages_only_reviewed_paths(git_repo):
    backend = ExecutionCenterCampaignBackend(api=EventAPI(), poll_seconds=0)
    (git_repo / "f.txt").write_text("changed\n")
    (git_repo / "new.py").write_text("VALUE = 1\n")
    manifest = backend._build_change_manifest(git_repo, timeout_seconds=10)
    assert [entry.path for entry in manifest] == ["f.txt", "new.py"]

    head = backend._commit(git_repo, "campaign", manifest, timeout_seconds=10)
    assert len(head) == 40
    changed = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert changed == ["f.txt", "new.py"]

    (git_repo / "outputs").mkdir()
    (git_repo / "outputs" / "leak.txt").write_text("secret")
    with pytest.raises(UnsafeCampaignChanges, match="outputs"):
        backend._build_change_manifest(git_repo, timeout_seconds=10)


def test_commit_revalidates_staged_blob_content_after_git_filters(git_repo):
    backend = ExecutionCenterCampaignBackend(api=EventAPI(), poll_seconds=0)
    (git_repo / ".gitattributes").write_text("f.txt filter=audit-rewrite\n")
    git(git_repo, "add", ".gitattributes")
    git(git_repo, "commit", "-m", "configure clean filter")
    git(git_repo, "config", "filter.audit-rewrite.clean", "sed s/changed/filtered/")
    git(git_repo, "config", "filter.audit-rewrite.smudge", "cat")
    git(git_repo, "config", "filter.audit-rewrite.required", "true")
    (git_repo / "f.txt").write_text("changed\n")
    manifest = backend._build_change_manifest(git_repo, timeout_seconds=10)

    with pytest.raises(UnsafeCampaignChanges, match="Staged blob content or mode"):
        backend._commit(git_repo, "campaign", manifest, timeout_seconds=10)


class ScenarioBackend(ExecutionCenterCampaignBackend):
    def __init__(self, root, *, gate_decision, fail_audit=False):
        super().__init__(api=EventAPI(), poll_seconds=0)
        self.root = root
        self.gate_decision = gate_decision
        self.fail_audit = fail_audit
        self.calls = []
        self.cleaned = []

    def _prepare_worktree(self, request):
        return self.root, "codex/test"

    def preflight(self, request):
        return {"provider_id": request.provider_id, "remote_main": "base-head"}

    def _head(self, worktree, *, timeout_seconds):
        return "base-head"

    def _launch(self, **kwargs):
        self.calls.append(("launch", kwargs["task_type"]))
        if self.fail_audit:
            raise CampaignBackendError("agent failed")
        return ({"id": kwargs["task_type"], "task_id": "task", "state": "COMPLETED"}, {}, "gate")

    def _validate(self, request, worktree):
        self.calls.append(("validate", None))
        return ("validator passed",)

    def _build_change_manifest(self, worktree, *, timeout_seconds):
        return ()

    def _review_diff(self, worktree, manifest, *, timeout_seconds):
        return "no changes"

    def _assess_gate(self, *args, **kwargs):
        return self.gate_decision

    def _cleanup_worktree(self, repository, worktree, *, timeout_seconds):
        self.cleaned.append(worktree)
        return True


def test_no_change_audit_is_valid_and_validation_precedes_final_gate(tmp_path):
    decision = GateDecision(approved=True, findings={}, reason="approved", evidence={})
    backend = ScenarioBackend(tmp_path, gate_decision=decision)
    result = backend.run(request(tmp_path))
    assert result.status == "completed"
    assert result.target_verified
    assert backend.calls == [
        ("launch", "audit"),
        ("validate", None),
        ("launch", "final_gate"),
    ]
    assert backend.cleaned == [tmp_path]


def test_clean_attention_and_failed_worktrees_are_cleaned(tmp_path):
    rejected = GateDecision(
        approved=False,
        findings={"High": ["unsafe"]},
        reason="high findings",
        evidence={},
    )
    attention = ScenarioBackend(tmp_path, gate_decision=rejected)
    assert attention.run(request(tmp_path)).status == "requires_attention"
    assert attention.cleaned == [tmp_path]

    failed = ScenarioBackend(tmp_path, gate_decision=rejected, fail_audit=True)
    with pytest.raises(CampaignBackendError, match="agent failed"):
        failed.run(request(tmp_path))
    assert failed.cleaned == [tmp_path]


def test_lease_fence_blocks_publication_after_final_gate(tmp_path):
    decision = GateDecision(approved=True, findings={}, reason="approved", evidence={})

    class PublishingScenario(ScenarioBackend):
        change = ChangeEntry(
            path="fix.py",
            status="untracked",
            size=6,
            sha256="a" * 64,
            mode=0o644,
        )

        def __init__(self, root):
            super().__init__(root, gate_decision=decision)
            self.committed = False

        def _build_change_manifest(self, worktree, *, timeout_seconds):
            return (self.change,)

        def _commit(self, *args, **kwargs):
            self.committed = True
            return "b" * 40

    checks = iter([True, True, True, False])
    backend = PublishingScenario(tmp_path)
    campaign_request = request(tmp_path, lease_check=lambda: next(checks))

    with pytest.raises(CampaignBackendError, match="losing its scheduler lease"):
        backend.run(campaign_request)

    assert not backend.committed
    assert backend.cleaned == [tmp_path]


class IntegrationAPI:
    def __init__(self):
        self.db_path = Path("/tmp/fake-runtime.db")
        self.results = {}
        self.counter = 0
        self.completion = None
        self.task_types = []
        self.start_kwargs = []

    def start_run(self, **kwargs):
        self.start_kwargs.append(kwargs)
        self.counter += 1
        run_id = f"run-{self.counter}"
        task_type = kwargs["task_type"]
        self.task_types.append(task_type)
        if task_type == "audit":
            Path(kwargs["repository_path"], "audit-fix.txt").write_text(
                "fixed\n", encoding="utf-8"
            )
            result = "User journey and queue waves exercised; repair is ready for review."
        else:
            instruction = kwargs["instruction"]

            def digest(label):
                match = re.search(rf"^{label}: ([0-9a-f]{{64}})$", instruction, re.MULTILINE)
                assert match
                return match.group(1)

            contract = {
                "approved": True,
                "verdict": "APPROVED FOR COMMIT",
                "findings": {"Blocker": [], "High": [], "Medium": [], "Low": []},
                "evidence": {
                    "diff_review": "Reviewed audit-fix.txt.",
                    "validation": "The supplied command exited zero.",
                    "user_journey": "The audit result records the user journey.",
                    "queue_waves": "The audit result records queue-wave coverage.",
                },
                "manifest_sha256": digest("MANIFEST SHA256"),
                "validation_sha256": digest("VALIDATION SHA256"),
                "campaign_evidence_sha256": digest("CAMPAIGN EVIDENCE SHA256"),
            }
            result = "DAILY_AUDIT_GATE_JSON: " + json.dumps(contract, separators=(",", ":"))
        self.results[run_id] = result
        return {"id": run_id}

    def get_run(self, run_id):
        return {
            "id": run_id,
            "state": "COMPLETED",
            "task_id": "task-1",
            "project": "AICC",
            "repository_path": "",
        }

    def get_events(self, run_id, *, after_seq=0, limit=1000):
        events = [
            {
                "seq": 1,
                "event_type": "result",
                "payload": {"result": self.results[run_id]},
            }
        ]
        return [event for event in events if event["seq"] > after_seq][:limit]

    def get_completion(self, run_id):
        return self.completion


def test_backend_preserves_isolated_worktree_commit_and_verified_completion_coverage(
    tmp_path, monkeypatch
):
    repository = repository_with_remote(tmp_path)
    api = IntegrationAPI()
    seeded = []

    class FakeOrchestrator:
        def __init__(self, db_path):
            assert db_path == api.db_path

        def begin_completion(self, run, *, task, project_cfg, policy_overrides=None):
            seeded.append((run, task, project_cfg, policy_overrides))
            api.completion = {
                "completion_state": "EXECUTION_FINISHED",
                "pull_request_url": None,
            }
            return api.completion

        def advance_safely(self, run_id):
            api.completion = {
                "completion_state": "COMPLETED",
                "pull_request_url": "https://example.test/pr/1",
            }
            return api.completion

    monkeypatch.setattr(
        "command_center.daily_audit_backend.CompletionOrchestrator",
        FakeOrchestrator,
    )
    backend = ExecutionCenterCampaignBackend(api, poll_seconds=0)
    monkeypatch.setattr(
        backend,
        "preflight",
        lambda request: {"provider_id": request.provider_id, "remote_main": "a" * 40},
    )
    campaign_request = request(
        repository,
        max_remediation_rounds=2,
        validation_commands=((sys.executable, "-c", "pass"),),
        lease_owner="daily-owner",
    )

    result = backend.run(campaign_request)

    assert result.status == "completed"
    assert result.target_verified
    assert result.pull_request_url == "https://example.test/pr/1"
    assert api.task_types == ["audit", "final_gate"]
    assert {item["executor_id"] for item in api.start_kwargs} == {"claude_code"}
    assert seeded[0][1]["merge_mode"] == "auto_after_checks"
    assert seeded[0][2]["validation_required"] is False
    assert "publication_fence_campaign_id" not in seeded[0][1]
    assert "publication_fence_owner" not in seeded[0][1]
    assert "publication_fence_campaign_id" not in seeded[0][2]
    assert "publication_fence_owner" not in seeded[0][2]
    assert seeded[0][3] == {
        "publication_fence_campaign_id": campaign_request.campaign_id,
        "publication_fence_owner": "daily-owner",
    }
    assert not (
        tmp_path / ".repo-daily-audit-worktrees" / campaign_request.campaign_id
    ).exists()
