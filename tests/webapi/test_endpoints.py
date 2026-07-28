"""Tests for `command_center.webapi.app.create_app` / `GET /api/home`.

Hermetic by construction: `create_app()`'s route handler constructs
`ExecutionCenterAPI()` and calls `build_workspace_home_snapshot` lazily,
only inside the request handler (never at import time), and both names are
resolved as plain module globals of `command_center.webapi.app` — so a test
can `monkeypatch.setattr(appmod, "build_workspace_home_snapshot", ...)` /
`monkeypatch.setattr(appmod, "ExecutionCenterAPI", ...)` before issuing a
request and the real ones are never touched. `ExecutionCenterAPI()`'s
constructor is *not* a no-op — `Supervisor.__init__` calls `db.migrate(...)`
against the real `runtime.db` (see `command_center/runtime/supervisor.py`) —
so both symbols are always monkeypatched here, never just one, to guarantee
no test in this file ever opens the real database.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from command_center.webapi.app import create_app


def _fake_snapshot(*, execution_center_api, **kwargs):
    assert execution_center_api == "fake-api-sentinel"
    return {
        "projects": [
            {
                "id": "AICC",
                "display_name": "AI Command Center",
                "sensitive": False,
                "repository_state": "ok",
                "task_count": 12,
                "active_run_count": 4,
            },
            {
                "id": "BANK",
                "display_name": "Bank",
                "sensitive": True,
                "repository_state": "ok",
                "task_count": 9,
                "active_run_count": 3,
            },
        ],
        "worktrees_by_project": {},
        "active_runs": [
            {
                "run_id": "r1",
                "source": "v2",
                "project": "AICC",
                "task_type": "implementation",
                "state": "RUNNING",
            }
        ],
        "recent_runs": [],
        "recent_activity": [],
        "artifacts": [],
        "reports": [],
    }


def _install_fakes(monkeypatch):
    import command_center.webapi.app as appmod

    monkeypatch.setattr(appmod, "build_workspace_home_snapshot", _fake_snapshot)
    monkeypatch.setattr(appmod, "ExecutionCenterAPI", lambda: "fake-api-sentinel")
    return appmod


def test_home_endpoint_returns_serialized(monkeypatch):
    _install_fakes(monkeypatch)
    client = TestClient(create_app())

    r = client.get("/api/home")

    assert r.status_code == 200
    body = r.json()
    # AICC's active_run_count (4) only — BANK's 3 is excluded from the
    # workspace-wide aggregate because BANK is sensitive (fix round 1).
    assert body["kpis"]["agents"]["value"] == 4
    assert set(body.keys()) == {"projects", "kpis", "queue", "health", "activity", "overview", "status"}


def test_home_endpoint_redacts_sensitive_project(monkeypatch):
    _install_fakes(monkeypatch)
    client = TestClient(create_app())

    body = client.get("/api/home").json()

    bank = next(p for p in body["projects"] if p["id"] == "BANK")
    assert bank["redacted"] is True
    assert "health" not in bank
    assert "task_count" not in bank


def test_home_endpoint_never_constructs_real_execution_center_api(monkeypatch):
    """Constructing the real `ExecutionCenterAPI` touches `runtime.db` (via
    `Supervisor.__init__`'s `db.migrate` call) — a test that hits it by
    accident would no longer be hermetic. Assert the app only ever calls the
    monkeypatched fake, never the real class."""
    import command_center.webapi.app as appmod

    calls: list[str] = []

    def fake_snapshot(*, execution_center_api, **kwargs):
        calls.append("snapshot")
        return {}

    def fake_api():
        calls.append("api")
        return object()

    monkeypatch.setattr(appmod, "build_workspace_home_snapshot", fake_snapshot)
    monkeypatch.setattr(appmod, "ExecutionCenterAPI", fake_api)

    client = TestClient(create_app())
    r = client.get("/api/home")

    assert r.status_code == 200
    assert calls == ["api", "snapshot"]
