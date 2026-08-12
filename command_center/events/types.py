"""Typed domain events for the Wave-1 in-process event bus.

Events are immutable value objects (frozen dataclasses). Each carries only the
identifiers and coarse routing facts a subscriber needs to react — never a full
entity payload, and never secrets, tokens, environment dumps or repository
paths. A subscriber that needs the entity re-reads it from its repository by id,
so the event stays a small, stable notification rather than a second, drifting
copy of the row.

The base :class:`Event` exists so a subscriber can register for *all* events
(subscribe to ``Event``) — delivery matches by ``isinstance`` (see
:mod:`command_center.events.bus`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from command_center.models import iso_now


@dataclass(frozen=True, slots=True)
class Event:
    """Base class for every bus event. ``occurred_at`` is an ISO-8601 local
    timestamp stamped at construction, matching the rest of the app's time
    convention (:func:`command_center.models.iso_now`)."""

    occurred_at: str = field(default_factory=iso_now)


@dataclass(frozen=True, slots=True)
class ProposalCreated(Event):
    """An advisor proposal (Советник) was persisted."""

    proposal_id: str = ""
    kind: str = ""
    project_ref: str = ""


@dataclass(frozen=True, slots=True)
class ProposalPromotedToTask(Event):
    """An advisor proposal was promoted into a task on the board. ``task_id`` is
    the id returned by the existing tasks path; downstream services (execution
    queue projections, digests, notifications) consume this."""

    proposal_id: str = ""
    task_id: str = ""
    project_ref: str = ""


@dataclass(frozen=True, slots=True)
class OwnerItemCreated(Event):
    """An item was added to «Мой день» (the owner's action list)."""

    item_id: str = ""
    title: str = ""


@dataclass(frozen=True, slots=True)
class DigestReady(Event):
    """A digest entry (Дайджест) was compiled and is ready to surface."""

    digest_id: str = ""
    category: str | None = None


@dataclass(frozen=True, slots=True)
class IncidentOpened(Event):
    """An operational incident was opened. Part of the typed event vocabulary
    the Wave-1 services publish/consume; the Incident entity itself is persisted
    in a later increment, but the event type is pinned here so subscribers can
    be written against a stable contract now."""

    incident_id: str = ""
    severity: str = ""
    project_ref: str | None = None
