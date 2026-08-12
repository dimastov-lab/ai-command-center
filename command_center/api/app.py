"""FastAPI application for the AI Command Center HTTP/JSON API.

Run it with::

    uvicorn command_center.api.app:app

``app`` is a module-level instance (built by :func:`create_app`) so the uvicorn
target above resolves directly; tests build their own isolated instance with
``create_app()`` and a :class:`fastapi.testclient.TestClient`.

Controllers only: every handler is a one-liner that delegates to
:mod:`command_center.api.service` (read paths) or
:mod:`command_center.api.wave1_service` (the Wave-1 write paths, mounted from
:mod:`command_center.api.wave1_routes`) and returns a typed
:mod:`command_center.api.schemas` / :mod:`command_center.api.models` model. No
business logic, no data access and no mutation lives in this module.

Versioning: **every** route is mounted under the ``/api/v1`` prefix. Pinning the
version before any client consumes the contract keeps a future ``/api/v2`` a
clean, additive move rather than a breaking rename of live paths.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from command_center.api import (
    audit_routes,
    conflict_routes,
    council_routes,
    marketplace_routes,
    model_registry_routes,
    schemas,
    service,
    wave1_routes,
)

# All read-only endpoints hang off one versioned router; the Wave-1 write
# endpoints live on their own router (also ``/api/v1``) and are included below.
_API_PREFIX = "/api/v1"


def _build_read_router() -> APIRouter:
    router = APIRouter(prefix=_API_PREFIX)

    @router.get("/health", response_model=schemas.HealthResponse)
    def health() -> schemas.HealthResponse:
        return service.get_health()

    @router.get("/dashboard", response_model=schemas.DashboardResponse)
    def dashboard() -> schemas.DashboardResponse:
        return service.build_dashboard()

    @router.get("/projects", response_model=list[schemas.Project])
    def projects() -> list[schemas.Project]:
        return service.list_projects()

    @router.get("/projects/{project_id}", response_model=schemas.Project)
    def project(project_id: str) -> schemas.Project:
        found = service.get_project(project_id)
        if found is None:
            raise HTTPException(status_code=404, detail="project not found")
        return found

    @router.get("/tasks", response_model=schemas.TaskList)
    def tasks(
        project: str | None = None, status: str | None = None
    ) -> schemas.TaskList:
        return service.list_tasks(project=project, status=status)

    # Declared before ``/tasks/{task_id}`` so the literal ``graph`` segment is
    # never captured as a task id by the parametrised route below.
    @router.get("/tasks/graph", response_model=schemas.TaskGraph)
    def tasks_graph(project: str | None = None) -> schemas.TaskGraph:
        return service.task_graph(project=project)

    @router.get("/tasks/{task_id}", response_model=schemas.Task)
    def task(task_id: str) -> schemas.Task:
        found = service.get_task(task_id)
        if found is None:
            raise HTTPException(status_code=404, detail="task not found")
        return found

    @router.get("/agents", response_model=schemas.AgentsResponse)
    def agents() -> schemas.AgentsResponse:
        return service.list_agents()

    return router


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Command Center API",
        version=service.get_health().version,
        summary="Backend for the desktop and mobile shells (v1).",
    )

    # Dev-only CORS for a locally served frontend (opt-in via env). Write verbs
    # are needed now that the Wave-1 surface accepts POSTs.
    if os.environ.get("AICC_API_DEV") == "1":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    app.include_router(_build_read_router())
    app.include_router(wave1_routes.router)
    app.include_router(conflict_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(council_routes.router)
    app.include_router(marketplace_routes.router)
    app.include_router(model_registry_routes.router)
    return app


app = create_app()
