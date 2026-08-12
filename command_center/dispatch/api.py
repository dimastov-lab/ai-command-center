"""FastAPI controller for the dispatch policy layer (`/api/v1/dispatch/*`).

Thin by construction — no business logic here. Each handler resolves the repo
root and delegates to `command_center.dispatch.service` / `.policy_config`,
then returns the value object's `as_dict()`. The service and policy-config
modules are referenced as **module globals** (`_service`, `_policy_config`) so a test can
`monkeypatch.setattr` them before issuing a request and never touch the real
board, runtime.db, or policy file — the same hermetic seam the existing
`webapi.app` uses for `ExecutionCenterAPI`.

Endpoints:

* `GET  /api/v1/dispatch/plan`   — dry run: what would be assigned + why.
* `POST /api/v1/dispatch/assign` — apply the plan (records executors; never launches).
* `GET  /api/v1/dispatch/policy` — the current config-driven policy.
* `PUT  /api/v1/dispatch/policy` — update limits/weights.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body

from command_center.dispatch import policy_config as _policy_config
from command_center.dispatch import service as _service

# Repo root is three levels up: <root>/command_center/dispatch/api.py
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _root() -> Path:
    """Resolved as a function (not a captured constant) so a test can
    monkeypatch this module's `_root` to point at an isolated temp tree."""
    return _REPO_ROOT


def create_dispatch_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])

    @router.get("/plan")
    def get_plan() -> dict:  # read-only, no mutation
        return _service.plan(_root()).as_dict()

    @router.post("/assign")
    def post_assign(payload: dict = Body(default=None)) -> dict:
        body = payload or {}
        return _service.assign(
            _root(),
            confirmed=bool(body.get("confirmed", False)),
            actor=body.get("actor"),
        )

    @router.get("/policy")
    def get_policy() -> dict:  # read-only, no mutation
        return _policy_config.load_policy(_root()).as_dict()

    @router.put("/policy")
    def put_policy(payload: dict = Body(default=None)) -> dict:
        body = payload or {}
        changes = body.get("changes", body)
        actor = body.get("actor") if isinstance(body, dict) else None
        return _policy_config.update_policy(_root(), changes, actor=actor).as_dict()

    return router
