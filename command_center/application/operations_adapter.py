"""Read/action adapter for native operational desktop sections."""

from __future__ import annotations

from command_center import project_config
from command_center.runtime import db as runtime_db
from command_center.runtime import scheduler
from command_center.runtime.api import ExecutionCenterAPI

from .workspace_home_adapter import WorkspaceHomeAdapter


class OperationsAdapter:
    """Expose existing runtime read models without putting domain logic in Qt."""

    def __init__(
        self,
        *,
        execution_center_api: ExecutionCenterAPI | None = None,
        workspace_home_adapter: WorkspaceHomeAdapter | None = None,
    ) -> None:
        self._workspace = workspace_home_adapter or WorkspaceHomeAdapter(
            execution_center_api=execution_center_api
        )
        self._api = self._workspace.execution_center_api

    def sessions(self) -> list[dict]:
        runs = self._api.list_runs(limit=500)
        latest_by_session: dict[str, dict] = {}
        for run in runs:
            session_id = run.get("session_id")
            if session_id and session_id not in latest_by_session:
                latest_by_session[session_id] = run
        rows = []
        for item in self._api.list_sessions()[:200]:
            latest = latest_by_session.get(item["id"], {})
            rows.append(
                {
                    "id": item["id"],
                    "project": item.get("project"),
                    "state": latest.get("state") or "UNKNOWN",
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
            )
        return rows

    def execution(self) -> list[dict]:
        snapshot = self._workspace.snapshot(
            active_runs_limit=100,
            recent_runs_limit=100,
            activity_limit=0,
            artifacts_limit=0,
            reports_limit=0,
        )
        return [*snapshot.get("active_runs", []), *snapshot.get("recent_runs", [])]

    def git(self) -> list[dict]:
        snapshot = self._workspace.snapshot(
            active_runs_limit=0,
            recent_runs_limit=0,
            activity_limit=0,
            artifacts_limit=0,
            reports_limit=0,
        )
        worktrees = snapshot.get("worktrees_by_project", {})
        rows = []
        for project in snapshot.get("projects", []):
            project_id = project["id"]
            sensitive = project_config.is_sensitive(project_id)
            discovered = worktrees.get(project_id, {}).get("worktrees", [])
            rows.append(
                {
                    "project": project_id,
                    "state": project.get("repository_state"),
                    "branch": None if sensitive else (discovered[0].get("branch") if discovered else None),
                    "path": None if sensitive else project.get("repository_path"),
                    "worktrees": len(discovered),
                }
            )
        return rows

    def artifacts(self) -> list[dict]:
        return self._workspace.snapshot(
            active_runs_limit=0,
            recent_runs_limit=0,
            activity_limit=0,
            artifacts_limit=200,
            reports_limit=0,
        ).get("artifacts", [])

    def reports(self) -> list[dict]:
        return self._workspace.snapshot(
            active_runs_limit=0,
            recent_runs_limit=0,
            activity_limit=0,
            artifacts_limit=0,
            reports_limit=200,
        ).get("reports", [])

    def agents(self) -> list[dict]:
        load = scheduler.build_load_snapshot(self._api.db_path)
        return [
            {
                **provider,
                "running": load.running_by_agent.get(provider["provider_id"], 0),
            }
            for provider in self._workspace.provider_capabilities()
        ]

    def cancel_run(self, run_id: str, *, confirmed: bool) -> dict:
        return self._api.request_cancel(run_id, confirmed=confirmed)

    @staticmethod
    def is_active_state(state: object) -> bool:
        return state in runtime_db.EXECUTION_CENTER_ACTIVE_STATES
