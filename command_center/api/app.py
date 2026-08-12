"""FastAPI application for the AI Command Center HTTP/JSON API.

Run it with::

    uvicorn command_center.api.app:app

``app`` is a module-level instance (built by :func:`create_app`) so the uvicorn
target above resolves directly; tests build their own isolated instance with
``create_app()`` and a :class:`fastapi.testclient.TestClient`.

Controllers only: every handler is a one-liner that delegates to
:mod:`command_center.api.service` and returns a typed
:mod:`command_center.api.schemas` model. No business logic, no data access, no
mutation lives here — this increment is entirely read-only and adds no writer,
per ``docs/AUTHORITY_MAP.md``.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from command_center.api import schemas, service


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Command Center API",
        version=service.get_health().version,
        summary="Read-only backend for the desktop and mobile shells.",
    )

    # Dev-only CORS for a locally served frontend (opt-in via env, GET-only).
    if os.environ.get("AICC_API_DEV") == "1":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @app.get("/api/health", response_model=schemas.HealthResponse)
    def health() -> schemas.HealthResponse:
        return service.get_health()

    @app.get("/api/dashboard", response_model=schemas.DashboardResponse)
    def dashboard() -> schemas.DashboardResponse:
        return service.build_dashboard()

    @app.get("/api/projects", response_model=list[schemas.Project])
    def projects() -> list[schemas.Project]:
        return service.list_projects()

    @app.get("/api/projects/{project_id}", response_model=schemas.Project)
    def project(project_id: str) -> schemas.Project:
        found = service.get_project(project_id)
        if found is None:
            raise HTTPException(status_code=404, detail="project not found")
        return found

    @app.get("/api/tasks", response_model=schemas.TaskList)
    def tasks(
        project: str | None = None, status: str | None = None
    ) -> schemas.TaskList:
        return service.list_tasks(project=project, status=status)

    @app.get("/api/tasks/{task_id}", response_model=schemas.Task)
    def task(task_id: str) -> schemas.Task:
        found = service.get_task(task_id)
        if found is None:
            raise HTTPException(status_code=404, detail="task not found")
        return found

    @app.get("/api/agents", response_model=schemas.AgentsResponse)
    def agents() -> schemas.AgentsResponse:
        return service.list_agents()

    return app


app = create_app()
