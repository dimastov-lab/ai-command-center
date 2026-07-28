import subprocess
import sys
from pathlib import Path

from command_center.daily_audit import CampaignRequest
from command_center.daily_audit_backend import ExecutionCenterCampaignBackend


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


class FakeAPI:
    def __init__(self):
        self.db_path = Path("/tmp/fake-runtime.db")
        self.results = {}
        self.counter = 0
        self.completion = None

    def start_run(self, **kwargs):
        self.counter += 1
        run_id = f"run-{self.counter}"
        task_type = kwargs["task_type"]
        if task_type == "implementation":
            Path(kwargs["repository_path"], "audit-fix.txt").write_text(
                "fixed\n", encoding="utf-8"
            )
            result = "READY FOR FINAL REVIEW"
        else:
            result = "APPROVED FOR COMMIT"
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

    def get_events(self, run_id):
        return [{"event_type": "result", "payload": {"result": self.results[run_id]}}]

    def get_completion(self, run_id):
        return self.completion

    def advance_completions(self, **kwargs):
        self.completion = {
            "completion_state": "COMPLETED",
            "pull_request_url": "https://example.test/pr/1",
        }


def test_backend_uses_isolated_worktree_final_gate_commit_and_verified_completion(
    tmp_path, monkeypatch
):
    repository = repository_with_remote(tmp_path)
    api = FakeAPI()
    seeded = []

    class FakeOrchestrator:
        def __init__(self, db_path):
            pass

        def begin_completion(self, run, *, task, project_cfg):
            seeded.append((run, task, project_cfg))
            api.completion = {
                "completion_state": "EXECUTION_FINISHED",
                "pull_request_url": None,
            }
            return {"completion_state": "EXECUTION_FINISHED"}

    monkeypatch.setattr(
        "command_center.daily_audit_backend.CompletionOrchestrator",
        FakeOrchestrator,
    )
    backend = ExecutionCenterCampaignBackend(api, poll_seconds=0)
    request = CampaignRequest(
        campaign_id="a" * 32,
        repository_path=repository,
        max_remediation_rounds=2,
        merge_mode="auto_after_checks",
        validation_commands=((sys.executable, "-c", "pass"),),
    )

    result = backend.run(request)

    assert result.status == "completed"
    assert result.target_verified
    assert result.pull_request_url == "https://example.test/pr/1"
    assert seeded[0][1]["merge_mode"] == "auto_after_checks"
    assert not (tmp_path / ".repo-daily-audit-worktrees" / request.campaign_id).exists()
