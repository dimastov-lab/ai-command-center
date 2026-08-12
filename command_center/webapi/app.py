"""FastAPI app factory for the AI Command Center web dashboard's backend.

Read-only for this slice: `GET /api/home` and `GET /api/execution` build the
Workspace Home read model (`workspace_home.build_workspace_home_snapshot`,
already redacted for BANK/LEGAL at the run/report/artifact/activity level)
and map it onto frontend DTOs via pure serializers (which
additionally closes the one redaction gap that function leaves open — see
`serializers.py`'s module docstring). No mutation, no other routes.

`build_workspace_home_snapshot` and `ExecutionCenterAPI` are imported as
plain module-level names and referenced unqualified inside the route
handler, specifically so a test can `monkeypatch.setattr(this_module, name,
fake)` and have the handler pick up the fake at call time — see
`tests/webapi/test_endpoints.py`. `ExecutionCenterAPI()` is constructed
lazily on the *first* request and then cached on `app.state` for reuse,
never at import time and never at `create_app()` time, because its
constructor has a real side effect: `Supervisor.__init__` runs
`db.migrate(...)` against the real `runtime.db`. Import-time or
app-factory-time construction would make every test that merely imports this
module, or calls `create_app()`, touch the real database; per-request
construction would re-run `db.migrate(...)` on every poll. Caching on the
per-app `app.state` (a fresh app per `create_app()`) keeps both properties:
each test gets its own empty cache and patches the module globals before its
first request, so the fake — never the real class — is what gets cached.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from command_center.dispatch.api import create_dispatch_router
from command_center.runtime.api import ExecutionCenterAPI
from command_center.webapi.serializers import serialize_execution, serialize_home
from command_center.workspace_home import build_workspace_home_snapshot

# Repo root is three levels up from this file: <root>/command_center/webapi/app.py
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIST = _REPO_ROOT / "web" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="AI Command Center API", docs_url=None, redoc_url=None)

    if os.environ.get("AICC_WEB_DEV") == "1":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @app.get("/api/home")
    def home() -> dict:  # read-only, no mutation
        # Build the ExecutionCenterAPI once per app and reuse it: its
        # constructor runs db.migrate(...) against runtime.db, so doing it
        # per request would re-migrate on every poll. Resolved as a module
        # global on first use so the test monkeypatch seam holds (see the
        # module docstring).
        api = getattr(app.state, "execution_center_api", None)
        if api is None:
            api = ExecutionCenterAPI()
            app.state.execution_center_api = api
        snapshot = build_workspace_home_snapshot(execution_center_api=api)
        return serialize_home(snapshot)

    @app.get("/api/execution")
    def execution() -> dict:  # read-only, no mutation
        api = getattr(app.state, "execution_center_api", None)
        if api is None:
            api = ExecutionCenterAPI()
            app.state.execution_center_api = api
        snapshot = build_workspace_home_snapshot(execution_center_api=api)
        return serialize_execution(snapshot)

    # Agent-dispatch policy layer (VOYN-W2-AGENT): `/api/v1/dispatch/*`.
    # Registered before the SPA mount so its routes resolve ahead of the
    # catch-all static handler.
    app.include_router(create_dispatch_router())

    # Serve the built SPA (built via `web/`'s `npm run build`) from the same
    # origin as the API, so production needs no CORS configuration. Mounted
    # AFTER the `/api` routes so `/api/home` resolves before the catch-all
    # static handler. Resolved relative to this file's location (not the
    # process CWD) so it works regardless of where the server is launched
    # from. Absent in dev (before a build, or under test), in which case the
    # API-only app above is served as-is.
    if _WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="spa")

    return app
