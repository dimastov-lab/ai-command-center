"""Typed skeletons for the "new engine" entities — contract only, no storage.

Wave 0 stood up the API service and, alongside it, made the *shape* of the
next wave's domain visible so both shells (and the eventual backend) agree on
one contract before any of it is persisted. Wave 1 (W1-DATA-EVENTS) wires the
first three — ``Proposal``, ``OwnerItem``, ``DigestItem`` — to a repository
(``runtime/db/wave1.py``), a table family (schema v15) and versioned routes
(``api/wave1_routes.py``); these serve as *response* models on that surface, so
their identity/routing fields (``id``; ``Proposal.project_ref``) are required
rather than defaulted. The remaining entities stay contract-only skeletons
until their own increment lands.

Grouping mirrors the product surfaces:

* Proposal            -- Советник (the advisor inbox).
* Motion/Vote/Decision -- Board (collective decisions on motions).
* AuditRun/AuditFinding -- automated audit runs and their findings.
* Incident            -- operational incidents.
* ModelEntry          -- the model catalog (external + local).
* OwnerItem           -- «Мой день» (the owner's action list).
* DigestItem          -- a periodic digest entry.

All values are placeholders with sensible defaults so a skeleton instance can
be constructed in a test without inventing every field. IDs are plain strings;
timestamps are ISO-8601 strings to match the rest of the read surface.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Советник — Proposal
# --------------------------------------------------------------------------

ProposalKind = Literal["trend", "ux", "optimization", "competitor", "feedback"]


class Proposal(BaseModel):
    """An advisor proposal surfaced to the owner for accept/dismiss.

    ``kind`` classifies where the suggestion came from (a spotted trend, a UX
    issue, an optimization, a competitor move, or inbound feedback).
    ``expected_gain`` and ``effort`` are the two-axis triage signals the
    Советник inbox sorts on. ``project_ref`` ties the proposal to a
    ``models.PROJECT_IDS`` namespace so it can be routed to the right board.
    """

    id: str
    kind: ProposalKind = "optimization"
    title: str = ""
    body: str = ""
    expected_gain: str | None = None
    effort: str | None = None
    project_ref: str
    status: Literal["new", "accepted", "dismissed", "converted"] = "new"
    created_at: str | None = None


# --------------------------------------------------------------------------
# Board — Motion / Vote / Decision
# --------------------------------------------------------------------------


class Motion(BaseModel):
    """A proposal put to the Board for a collective decision."""

    id: str = ""
    title: str = ""
    body: str = ""
    project_ref: str | None = None
    proposal_ref: str | None = None
    status: Literal["open", "decided", "withdrawn"] = "open"
    created_at: str | None = None


class Vote(BaseModel):
    """One board member's vote on a :class:`Motion`."""

    id: str = ""
    motion_ref: str = ""
    voter: str = ""
    value: Literal["for", "against", "abstain"] = "abstain"
    rationale: str | None = None
    created_at: str | None = None


class Decision(BaseModel):
    """The resolved outcome of a :class:`Motion` once voting closes."""

    id: str = ""
    motion_ref: str = ""
    outcome: Literal["approved", "rejected", "deferred"] = "deferred"
    summary: str = ""
    decided_at: str | None = None


# --------------------------------------------------------------------------
# Audit — AuditRun / AuditFinding
# --------------------------------------------------------------------------

#: The check families an audit run selects from. Each is a pluggable collector
#: behind the audit ``CheckRegistry`` — mirrors ``command_center.audit`` and the
#: ``audit_finding.category`` column.
AuditCategory = Literal["security", "coverage", "code-quality", "deps", "lint"]

#: A finding's severity, ordered least→most urgent.
AuditSeverity = Literal["info", "low", "medium", "high", "critical"]

#: A finding's triage lifecycle. ``open`` is the initial state every finding is
#: written in; ``ack`` (acknowledged) and ``fixed`` are the operator-driven moves.
AuditFindingStatus = Literal["open", "ack", "fixed"]

#: An audit run's execution lifecycle.
AuditRunStatus = Literal["queued", "running", "completed", "failed"]


class AuditRun(BaseModel):
    """One execution of an automated audit over a project.

    ``checks`` records which check families ran on this pass; ``finding_count``
    is the number of findings persisted. ``project_ref`` ties the run to a
    ``models.PROJECT_IDS`` namespace so it can be redacted when that project is
    sensitive. Routed entity: ``id`` is required — a response never carries an
    empty id."""

    id: str
    project_ref: str
    status: AuditRunStatus = "queued"
    checks: list[str] = Field(default_factory=list)
    finding_count: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None


class AuditFinding(BaseModel):
    """A single finding produced by an :class:`AuditRun`.

    Every finding *always* carries a ``status`` and an ``owner`` — the write
    path refuses a finding with no owner, so the two triage axes can never be
    missing on the surface (VOYN-W2-AUD acceptance). ``file_path``/``loc``
    locate the finding in the tree; ``category`` says which check produced it."""

    id: str
    run_id: str
    category: AuditCategory
    severity: AuditSeverity = "info"
    summary: str = ""
    file_path: str | None = None
    loc: str | None = None
    status: AuditFindingStatus = "open"
    owner: str
    project_ref: str | None = None
    created_at: str | None = None


# --------------------------------------------------------------------------
# Incident
# --------------------------------------------------------------------------


class Incident(BaseModel):
    """An operational incident tracked to resolution."""

    id: str = ""
    title: str = ""
    severity: Literal["sev1", "sev2", "sev3", "sev4"] = "sev3"
    status: Literal["open", "mitigated", "resolved"] = "open"
    project_ref: str | None = None
    opened_at: str | None = None
    resolved_at: str | None = None


# --------------------------------------------------------------------------
# Conflicts / Incidents engine — Conflict
# --------------------------------------------------------------------------

ConflictKind = Literal["merge", "perf", "budget", "security"]


class Conflict(BaseModel):
    """A tracked conflict/incident moving through open → mitigating → resolved.

    ``kind`` classifies the friction (a ``merge`` collision, a ``perf``
    regression, a ``budget`` overrun, a ``security`` exposure). ``source_ref`` is
    the opaque origin reference the conflict was opened from (e.g.
    ``incident:<id>``, a PR ref) and is what the BANK/LEGAL redaction protects —
    a conflict whose ``project_ref`` is sensitive is dropped from every read so
    its ``source_ref`` never leaves the surface. ``owner`` and ``mitigation`` are
    the two facts a conflict must carry before it may reach ``resolved`` (the
    invariant is enforced in the service, not the DB).
    """

    id: str = ""
    kind: ConflictKind = "merge"
    source_ref: str = ""
    severity: Literal["sev1", "sev2", "sev3", "sev4"] = "sev3"
    status: Literal["open", "mitigating", "resolved"] = "open"
    owner: str | None = None
    mitigation: str | None = None
    project_ref: str | None = None
    opened_at: str | None = None
    resolved_at: str | None = None


# --------------------------------------------------------------------------
# Model catalog — ModelEntry / ModelEvent
# --------------------------------------------------------------------------

#: An entry's provenance kind: a hosted external API provider vs. a local model.
ModelKind = Literal["external", "local"]

#: A model's availability lifecycle. ``available`` is the initial state (an
#: external model is available the moment it is registered; a local model starts
#: available and moves through the download lifecycle). ``downloading`` and
#: ``installed`` are the local-download milestones; ``error`` is the failure
#: state a download or probe can land in.
ModelStatus = Literal["available", "downloading", "installed", "error"]

#: The governance-log action vocabulary. A model's *history* is the ordered list
#: of these actions (VOYN-W3 traceability acceptance).
ModelAction = Literal[
    "register", "download-request", "download-progress", "assign", "use", "status-change"
]


class ModelEntry(BaseModel):
    """One entry in the AI-model catalog — an external (hosted API) model or a
    local one. Routed entity: ``id`` is required (a response never carries an
    empty id).

    ``kind`` distinguishes external from local; ``status`` is the current point
    in its availability lifecycle, and ``download_progress`` (0..100) tracks a
    local model's download. ``cost``/``quality``/``latency_ms`` are the metadata
    the auto-select helper weighs (prefer local for cost); ``provenance`` records
    where the model came from so its origin is traceable."""

    id: str
    name: str = ""
    kind: ModelKind = "external"
    provider: str | None = None
    status: ModelStatus = "available"
    cost: float | None = None
    quality: float | None = None
    latency_ms: int | None = None
    provenance: str | None = None
    download_progress: int = 0
    created_at: str | None = None


class ModelEvent(BaseModel):
    """One entry in a model's governance log — the append-only record that makes
    its history fully traceable. ``seq`` orders the log per model; ``action`` is
    what happened; ``target_ref`` is the opaque task/agent an ``assign``/``use``
    acted on; ``provenance`` and ``metadata`` carry the coarse facts of the move."""

    model_config = {"protected_namespaces": ()}

    seq: int
    model_id: str
    action: ModelAction
    actor: str | None = None
    target_ref: str | None = None
    provenance: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str | None = None


# --------------------------------------------------------------------------
# «Мой день» — OwnerItem / DigestItem
# --------------------------------------------------------------------------


class OwnerItem(BaseModel):
    """One item on the owner's day list — a thing only the owner can action."""

    id: str
    title: str = ""
    detail: str | None = None
    due: str | None = None
    done: bool = False
    source_ref: str | None = None
    created_at: str | None = None


class DigestItem(BaseModel):
    """One entry in a periodic digest (daily/weekly rollup)."""

    id: str
    title: str = ""
    body: str = ""
    category: str | None = None
    refs: list[str] = Field(default_factory=list)
    created_at: str | None = None
