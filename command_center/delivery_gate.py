"""Fail-closed evidence gate for preparing a manual delivery decision.

The gate never merges, deploys, closes a pull request, or starts a follow-on
action. It only answers whether a human may review an exact candidate SHA with
completed CI evidence. Unknown, pending, mismatched, and automatic chains stay
blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CheckEvidence:
    name: str
    status: str
    conclusion: str | None
    head_sha: str | None


@dataclass(frozen=True)
class DeliveryDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_delivery(
    *,
    candidate_sha: str,
    pull_request_head_sha: str | None,
    checks: Iterable[CheckEvidence],
    auto_complete_requested: bool = False,
) -> DeliveryDecision:
    """Evaluate immutable evidence without performing a delivery action."""
    reasons: list[str] = []
    if not candidate_sha or pull_request_head_sha != candidate_sha:
        reasons.append("pull_request_head_sha_mismatch")

    observed = list(checks)
    if not observed:
        reasons.append("ci_missing")
    for check in observed:
        label = check.name or "unnamed_check"
        if check.head_sha != candidate_sha:
            reasons.append(f"{label}:head_sha_mismatch")
        elif check.status.upper() != "COMPLETED":
            reasons.append(f"{label}:pending")
        elif (check.conclusion or "").upper() != "SUCCESS":
            reasons.append(f"{label}:failure")

    if auto_complete_requested:
        reasons.append("auto_complete_forbidden")

    return DeliveryDecision(allowed=not reasons, reasons=tuple(reasons))
