"""Application-layer adapter for the Workspace Home read model (Desktop D2A).

`WorkspaceHomeAdapter` owns the one application-wide :class:`ExecutionCenterAPI`
(`docs/desktop/ARCHITECTURE.md` §3, §5) and exposes the Workspace Home snapshot to
the presentation layer. It is a *thin* wrapper: it returns
:func:`build_workspace_home_snapshot`'s dict unchanged — no field is dropped,
added, or transformed — so every sensitivity-redaction guarantee of the read model
is inherited verbatim. No Qt import lives here.
"""

from __future__ import annotations

from command_center.application.aios_status import (
    AIOSStatusClient,
    create_aios_status_client,
)
from command_center.runtime.api import ExecutionCenterAPI
from command_center.workspace_home import build_workspace_home_snapshot


class WorkspaceHomeAdapter:
    """Adapter owning the single ``ExecutionCenterAPI`` for Workspace Home reads.

    Pass an explicit ``execution_center_api`` in tests to point it at an isolated
    runtime database; in the running application it is constructed once and shared.
    """

    def __init__(
        self,
        *,
        execution_center_api: ExecutionCenterAPI | None = None,
        aios_status_client: AIOSStatusClient | None = None,
    ) -> None:
        self._api = execution_center_api if execution_center_api is not None else ExecutionCenterAPI()
        self._aios_status_client = aios_status_client or create_aios_status_client()

    @property
    def execution_center_api(self) -> ExecutionCenterAPI:
        """The one ``ExecutionCenterAPI`` this adapter reads through."""
        return self._api

    def snapshot(
        self,
        *,
        active_runs_limit: int = 20,
        recent_runs_limit: int = 20,
        activity_limit: int = 20,
        artifacts_limit: int = 20,
        reports_limit: int = 20,
    ) -> dict:
        """Return the local read model plus a separately sourced AIOS status."""
        snapshot = build_workspace_home_snapshot(
            execution_center_api=self._api,
            active_runs_limit=active_runs_limit,
            recent_runs_limit=recent_runs_limit,
            activity_limit=activity_limit,
            artifacts_limit=artifacts_limit,
            reports_limit=reports_limit,
        )
        status = self._aios_status_client.get_core_status()
        return {
            **snapshot,
            "aios_core": {
                "readiness": status.readiness.value,
                "source": status.source,
                "version": status.version,
                "health": status.health,
                "capabilities": list(status.capabilities),
                "gates": list(status.gates),
                "evidence": list(status.evidence),
                "detail": status.detail,
            },
        }
