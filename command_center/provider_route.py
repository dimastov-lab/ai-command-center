"""Explicit, fail-closed provider routing with immutable attempt evidence.

The ordered route is fixed before execution. Each provider can appear once,
and only a provider-local transient failure with verified unchanged workspace
may advance to the next provider. No exception text, prompt, or credential is
persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, TypeVar

from command_center.models import iso_now
from command_center.runtime import db

TRANSIENT = "transient"
AUTHENTICATION = "authentication"
POLICY = "policy"
INVALID_REQUEST = "invalid_request"
CANCELLED = "cancelled"
TIMEOUT = "timeout"
INCOMPLETE = "incomplete"
UNKNOWN = "unknown"
SUCCESS = "success"

RETRY_NEXT_PROVIDER = "retry_next_provider"
TERMINAL = "terminal"
SUCCEEDED = "succeeded"

_TRANSIENT_CODES = frozenset({"provider_api_error", "network_error", "rate_limited"})


class UnauthorizedProviderError(ValueError):
    """An explicit route requested a provider outside project policy."""


@dataclass(frozen=True)
class WorkspaceEvidence:
    before_head: str
    after_head: str
    before_tree: str
    after_tree: str

    @property
    def unchanged_verified(self) -> bool:
        return bool(
            self.before_head
            and self.before_tree
            and self.before_head == self.after_head
            and self.before_tree == self.after_tree
        )


@dataclass(frozen=True)
class ProviderRoute:
    providers: tuple[str, ...]
    max_attempts: int
    selection_reason: str = "explicit_request"
    policy_version: str = "project_policy_v1"

    def __post_init__(self) -> None:
        if not self.providers or any(not item for item in self.providers):
            raise ValueError("Provider route must contain non-empty provider ids")
        if len(set(self.providers)) != len(self.providers):
            raise ValueError("Provider route may attempt each provider at most once")
        if not 1 <= self.max_attempts <= len(self.providers):
            raise ValueError("max_attempts must fit inside the provider route")
        if not self.selection_reason or not self.policy_version:
            raise ValueError("Provider route reason and policy version are required")

    @classmethod
    def from_policy(
        cls,
        *,
        allowed_providers: tuple[str, ...],
        preferred_provider: str | None,
        policy_version: str,
        explicit_providers: tuple[str, ...] | None = None,
    ) -> ProviderRoute:
        """Filter policy first, then order the already-authorized candidates."""
        allowed = tuple(dict.fromkeys(item for item in allowed_providers if item))
        requested = explicit_providers or allowed
        unauthorized = [item for item in requested if item not in allowed]
        if unauthorized:
            raise UnauthorizedProviderError(
                f"Provider is not authorized by {policy_version}: {unauthorized[0]}"
            )
        candidates = tuple(dict.fromkeys(requested))
        if not candidates:
            raise UnauthorizedProviderError(f"No providers authorized by {policy_version}")
        if preferred_provider and preferred_provider in candidates:
            candidates = (preferred_provider,) + tuple(
                item for item in candidates if item != preferred_provider
            )
            reason = "policy_filtered_preference"
        else:
            reason = "policy_order"
        return cls(
            candidates,
            max_attempts=len(candidates),
            selection_reason=reason,
            policy_version=policy_version,
        )


class ProviderFailure(Exception):
    def __init__(
        self,
        error_code: str,
        classification: str,
        *,
        workspace_evidence: WorkspaceEvidence | None = None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.classification = classification
        self.workspace_evidence = workspace_evidence


def classify_failure(error_code: str | None) -> str:
    """Classify only stable provider-local codes; unknowns stay terminal."""
    if error_code in _TRANSIENT_CODES:
        return TRANSIENT
    if error_code in {"authentication_failed", "session_expired"}:
        return AUTHENTICATION
    if error_code == "invalid_request":
        return INVALID_REQUEST
    if error_code == "timeout":
        return TIMEOUT
    if error_code == "cancelled":
        return CANCELLED
    if error_code and (
        error_code.startswith("blocked:") or error_code in {"policy_denied", "permission_denied"}
    ):
        return POLICY
    if error_code and error_code.startswith("incomplete:"):
        return INCOMPLETE
    return UNKNOWN


@dataclass(frozen=True)
class ProviderAttempt:
    attempt_number: int
    provider_id: str
    outcome: str
    classification: str
    disposition: str
    error_code: str | None
    parent_attempt_number: int | None
    started_at: str
    completed_at: str

    @classmethod
    def from_record(cls, record: dict) -> ProviderAttempt:
        return cls(
            attempt_number=record["attempt_number"],
            provider_id=record["provider_id"],
            outcome=record["outcome"],
            classification=record["classification"],
            disposition=record["disposition"],
            error_code=record["error_code"],
            parent_attempt_number=record["parent_attempt_number"],
            started_at=record["started_at"],
            completed_at=record["completed_at"],
        )


class RouteExecutionFailed(RuntimeError):
    def __init__(
        self,
        *,
        error_code: str,
        attempts: tuple[ProviderAttempt, ...],
        exhausted: bool,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.attempts = attempts
        self.exhausted = exhausted


_T = TypeVar("_T")


@dataclass(frozen=True)
class RouteExecutionResult(Generic[_T]):
    value: _T
    attempts: tuple[ProviderAttempt, ...]


def _stored_route_matches(db_path: Path, run_id: str, route: ProviderRoute) -> bool:
    stored = db.get_provider_route(db_path, run_id)
    return stored is not None and (
        tuple(stored["providers"]),
        stored["max_attempts"],
        stored["selection_reason"],
        stored["policy_version"],
    ) == (
        route.providers,
        route.max_attempts,
        route.selection_reason,
        route.policy_version,
    )


def execute_for_run(
    db_path: Path,
    run_id: str,
    route: ProviderRoute,
    operation: Callable[[str, int], _T],
    *,
    clock: Callable[[], str] = iso_now,
) -> RouteExecutionResult[_T]:
    """Execute a hermetic/provider adapter operation against a fixed route."""
    if not _stored_route_matches(db_path, run_id, route):
        raise ValueError("Provider route differs from immutable run provenance")

    attempts: list[ProviderAttempt] = []
    for attempt_number, provider_id in enumerate(
        route.providers[: route.max_attempts], start=1
    ):
        db.start_provider_attempt(
            db_path,
            run_id=run_id,
            attempt_number=attempt_number,
            provider_id=provider_id,
            started_at=clock(),
        )
        try:
            value = operation(provider_id, attempt_number)
        except ProviderFailure as exc:
            classification = classify_failure(exc.error_code)
            can_advance = (
                classification == TRANSIENT
                and exc.workspace_evidence is not None
                and exc.workspace_evidence.unchanged_verified
                and attempt_number < route.max_attempts
            )
            record = db.finish_provider_attempt(
                db_path,
                run_id=run_id,
                attempt_number=attempt_number,
                outcome="failed",
                classification=classification,
                disposition=RETRY_NEXT_PROVIDER if can_advance else TERMINAL,
                error_code=exc.error_code,
                completed_at=clock(),
            )
            attempts.append(ProviderAttempt.from_record(record))
            if can_advance:
                continue
            raise RouteExecutionFailed(
                error_code=exc.error_code,
                attempts=tuple(attempts),
                exhausted=(
                    classification == TRANSIENT
                    and exc.workspace_evidence is not None
                    and exc.workspace_evidence.unchanged_verified
                    and attempt_number == route.max_attempts
                ),
            ) from None
        except Exception:
            record = db.finish_provider_attempt(
                db_path,
                run_id=run_id,
                attempt_number=attempt_number,
                outcome="failed",
                classification=UNKNOWN,
                disposition=TERMINAL,
                error_code="unexpected_provider_error",
                completed_at=clock(),
            )
            attempts.append(ProviderAttempt.from_record(record))
            raise RouteExecutionFailed(
                error_code="unexpected_provider_error",
                attempts=tuple(attempts),
                exhausted=False,
            ) from None
        record = db.finish_provider_attempt(
            db_path,
            run_id=run_id,
            attempt_number=attempt_number,
            outcome="succeeded",
            classification=SUCCESS,
            disposition=SUCCEEDED,
            error_code=None,
            completed_at=clock(),
        )
        attempts.append(ProviderAttempt.from_record(record))
        return RouteExecutionResult(value=value, attempts=tuple(attempts))

    raise AssertionError("Validated provider route unexpectedly had no attempts")
