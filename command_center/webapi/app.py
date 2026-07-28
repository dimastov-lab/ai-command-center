"""FastAPI app factory for the AI Command Center web dashboard's backend.

Read-only for this slice: the only route is `GET /api/home`, which builds the
Workspace Home read model (`workspace_home.build_workspace_home_snapshot`,
already redacted for BANK/LEGAL at the run/report/artifact/activity level)
and maps it onto the frontend's DTO via `serializers.serialize_home` (which
additionally closes the one redaction gap that function leaves open — see
`serializers.py`'s module docstring). No mutation, no other routes.

`build_workspace_home_snapshot` and `ExecutionCenterAPI` are imported as
plain module-level names and referenced unqualified inside the route
handler, specifically so a test can `monkeypatch.setattr(this_module, name,
fake)` and have the handler pick up the fake at call time — see
`tests/webapi/test_endpoints.py`. `ExecutionCenterAPI()` is constructed
lazily inside the handler (never at import time, never at `create_app()`
time) because its constructor has a real side effect: `Supervisor.__init__`
runs `db.migrate(...)` against the real `runtime.db`. Import-time or
app-factory-time construction would make every test that merely imports this
module, or calls `create_app()`, touch the real database.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from command_center.runtime.api import ExecutionCenterAPI
from command_center.webapi.serializers import serialize_home
from command_center.workspace_home import build_workspace_home_snapshot


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
        api = ExecutionCenterAPI()
        snapshot = build_workspace_home_snapshot(execution_center_api=api)
        return serialize_home(snapshot)

    return app
