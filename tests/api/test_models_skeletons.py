"""Contract tests for the new-engine model skeletons (``command_center.api.models``).

These entities have no persistence yet; the test only pins the *shape* — that
each skeleton constructs with defaults, and that the constrained fields
(``Proposal.kind``, ``ModelEntry.source``) reject out-of-contract values — so a
later increment that wires them to storage cannot silently drift the contract
the shells were built against.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from command_center.api import models


def test_all_skeletons_construct_with_defaults() -> None:
    for name in (
        "Proposal", "Motion", "Vote", "Decision", "AuditRun", "AuditFinding",
        "Incident", "ModelEntry", "OwnerItem", "DigestItem",
    ):
        cls = getattr(models, name)
        instance = cls()
        assert instance.model_dump() is not None


def test_proposal_kind_is_constrained() -> None:
    for kind in ("trend", "ux", "optimization", "competitor", "feedback"):
        assert models.Proposal(kind=kind).kind == kind
    with pytest.raises(ValidationError):
        models.Proposal(kind="not-a-kind")


def test_model_entry_source_is_constrained() -> None:
    assert models.ModelEntry(source="external").source == "external"
    assert models.ModelEntry(source="local").source == "local"
    with pytest.raises(ValidationError):
        models.ModelEntry(source="cloud")


def test_proposal_carries_advisor_triage_fields() -> None:
    proposal = models.Proposal(
        kind="trend", title="Adopt X", body="…", expected_gain="high",
        effort="low", project_ref="AICC",
    )
    assert proposal.expected_gain == "high"
    assert proposal.effort == "low"
    assert proposal.project_ref == "AICC"
