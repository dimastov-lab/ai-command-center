"""HTTP/JSON API service for the AI Command Center (Wave 0, F-1).

A read-only FastAPI application (``uvicorn command_center.api.app:app``) that
turns the desktop and future mobile shells into thin clients over one backend.
It is a *pure addition*: every endpoint is built ON the existing, already
audited read paths (the Integration Center registry + health collectors, the
tasks repository, the runtime run read model, git readers) — no Streamlit code
is touched, no supervisor/db semantics change, and this package introduces no
new writer.

Layering (per the project's Controller -> Service -> Repository rule):

    app.py      -- FastAPI routes (controllers); no business logic
    service.py  -- read-only aggregation over the existing modules
    schemas.py  -- Pydantic response models (the wire contract both shells use)
    models.py   -- typed skeletons for the not-yet-built "new engine" entities

``models.py`` deliberately carries no persistence: it exists so the contract
for Proposals (Советник), Board motions/votes/decisions, audit runs, incidents,
the model catalog, and the owner day/digest surfaces is visible now, before any
of it is wired to storage.
"""
