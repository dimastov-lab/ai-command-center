"""HTTP routes for the Wave-1 write surface (Советник / Мой день / Дайджест).

Controllers only: each handler is a thin adapter that validates its inputs via
FastAPI, delegates to exactly one :mod:`command_center.api.wave1_service`
function, and maps a ``None``/domain error onto the right HTTP status. No
business logic, no data access, and no event publishing live here.

Mounted under the versioned ``/api/v1`` prefix (see ``api/app.py``); every path
below is relative to that.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from command_center.api import models
from command_center.api import wave1_schemas as w
from command_center.api import wave1_service as service

router = APIRouter(prefix="/api/v1", tags=["wave1"])

# Shared paging bounds for every list endpoint on this surface.
_MAX_LIMIT = 500


# --------------------------------------------------------------------------
# Советник — proposals
# --------------------------------------------------------------------------


@router.get("/proposals", response_model=w.ProposalList)
def list_proposals(
    project: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> w.ProposalList:
    return service.list_proposals(
        project=project, status=status, limit=limit, offset=offset
    )


@router.get("/proposals/{proposal_id}", response_model=models.Proposal)
def get_proposal(proposal_id: str) -> models.Proposal:
    found = service.get_proposal(proposal_id)
    if found is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return found


@router.post("/proposals", response_model=models.Proposal, status_code=201)
def create_proposal(payload: w.ProposalCreate) -> models.Proposal:
    return service.create_proposal(payload)


@router.post("/proposals/{proposal_id}/promote", response_model=w.PromoteResponse)
def promote_proposal(proposal_id: str) -> w.PromoteResponse:
    try:
        result = service.promote_proposal(proposal_id)
    except service.ProposalNotPromotableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return result


# --------------------------------------------------------------------------
# «Мой день» — owner items
# --------------------------------------------------------------------------


@router.get("/owner-items", response_model=w.OwnerItemList)
def list_owner_items(
    done: bool | None = None,
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> w.OwnerItemList:
    return service.list_owner_items(done=done, limit=limit, offset=offset)


@router.get("/owner-items/{item_id}", response_model=models.OwnerItem)
def get_owner_item(item_id: str) -> models.OwnerItem:
    found = service.get_owner_item(item_id)
    if found is None:
        raise HTTPException(status_code=404, detail="owner item not found")
    return found


@router.post("/owner-items", response_model=models.OwnerItem, status_code=201)
def create_owner_item(payload: w.OwnerItemCreate) -> models.OwnerItem:
    return service.create_owner_item(payload)


# --------------------------------------------------------------------------
# Дайджест — digest items
# --------------------------------------------------------------------------


@router.get("/digest", response_model=w.DigestItemList)
def list_digest_items(
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> w.DigestItemList:
    return service.list_digest_items(category=category, limit=limit, offset=offset)


@router.get("/digest/{item_id}", response_model=models.DigestItem)
def get_digest_item(item_id: str) -> models.DigestItem:
    found = service.get_digest_item(item_id)
    if found is None:
        raise HTTPException(status_code=404, detail="digest item not found")
    return found


@router.post("/digest", response_model=models.DigestItem, status_code=201)
def create_digest_item(payload: w.DigestItemCreate) -> models.DigestItem:
    return service.create_digest_item(payload)
