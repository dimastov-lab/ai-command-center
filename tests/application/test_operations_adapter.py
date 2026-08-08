from types import SimpleNamespace

from command_center.application.operations_adapter import OperationsAdapter


class FakeAPI:
    db_path = "runtime.db"

    def list_runs(self, **_kwargs):
        return [{"session_id": "s1", "state": "RUNNING"}]

    def list_sessions(self):
        return [{"id": "s1", "project": "AIOS", "created_at": "t1", "updated_at": "t2", "repository_path": "/secret"}]


class FakeWorkspace:
    execution_center_api = FakeAPI()

    def snapshot(self, **kwargs):
        if kwargs.get("artifacts_limit"):
            return {"artifacts": [{"project": "AIOS", "path": "/artifact"}]}
        if kwargs.get("reports_limit"):
            return {"reports": [{"project": "AIOS", "path": "/report"}]}
        return {
            "active_runs": [{"project": "AIOS", "state": "RUNNING"}],
            "recent_runs": [{"project": "AIOS", "state": "COMPLETED"}],
            "projects": [
                {"id": "AIOS", "repository_state": "ok", "repository_path": "/aios"},
                {"id": "BANK", "repository_state": "ok", "repository_path": "/bank"},
            ],
            "worktrees_by_project": {
                "AIOS": {"worktrees": [{"branch": "main"}]},
                "BANK": {"worktrees": [{"branch": "classified"}]},
            },
        }

    def provider_capabilities(self):
        return [{"provider_id": "codex", "display_name": "Codex", "readiness": "available", "detail": ""}]


def test_sessions_expose_operational_fields_only():
    rows = OperationsAdapter(workspace_home_adapter=FakeWorkspace()).sessions()
    assert rows == [{"id": "s1", "project": "AIOS", "state": "RUNNING", "created_at": "t1", "updated_at": "t2"}]


def test_git_redacts_sensitive_paths_and_branches():
    rows = OperationsAdapter(workspace_home_adapter=FakeWorkspace()).git()
    by_project = {row["project"]: row for row in rows}
    assert by_project["AIOS"]["path"] == "/aios"
    assert by_project["AIOS"]["branch"] == "main"
    assert by_project["BANK"]["path"] is None
    assert by_project["BANK"]["branch"] is None


def test_agents_include_live_load(monkeypatch):
    monkeypatch.setattr(
        "command_center.application.operations_adapter.scheduler.build_load_snapshot",
        lambda _path: SimpleNamespace(running_by_agent={"codex": 2}),
    )
    rows = OperationsAdapter(workspace_home_adapter=FakeWorkspace()).agents()
    assert rows[0]["running"] == 2
