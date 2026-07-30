"""Read-only AIOS Core status boundary for the desktop control plane."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from collections.abc import Mapping
from typing import Callable, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen


class AIOSCoreReadiness(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    OFFLINE = "offline"
    ERROR = "error"
    CONTRACT_PENDING = "contract_pending"


@dataclass(frozen=True)
class AIOSCoreStatus:
    readiness: AIOSCoreReadiness
    source: str
    version: str | None = None
    health: str | None = None
    capabilities: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    detail: str | None = None


class AIOSStatusClient(Protocol):
    def get_core_status(self) -> AIOSCoreStatus: ...


class DisabledAIOSStatusClient:
    """Safe default until a stable public AIOS status contract is configured."""

    def get_core_status(self) -> AIOSCoreStatus:
        return AIOSCoreStatus(
            readiness=AIOSCoreReadiness.CONTRACT_PENDING,
            source="configuration",
            evidence=("Публичный контракт AIOS Core ожидается",),
        )


class HTTPAIOSStatusClient:
    """Transport for the proposed versioned, read-only AIOS status endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        opener: Callable[..., object] = urlopen,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._endpoint = endpoint
        self._opener = opener
        self._timeout_seconds = timeout_seconds

    def get_core_status(self) -> AIOSCoreStatus:
        request = Request(self._endpoint, headers={"Accept": "application/json"})
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:  # type: ignore[attr-defined]
                payload = json.loads(response.read())
        except URLError:
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.OFFLINE,
                source="AIOS API",
                detail="Публичный API AIOS Core недоступен",
            )
        except json.JSONDecodeError:
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.ERROR,
                source="AIOS API",
                detail="Ответ AIOS не соответствует ожидаемому контракту",
            )
        try:
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness(payload["readiness"]),
                source="AIOS API",
                version=payload.get("version"),
                health=payload.get("health"),
                capabilities=tuple(payload.get("capabilities", ())),
                gates=tuple(payload.get("gates", ())),
                evidence=tuple(payload.get("evidence", ())),
            )
        except (KeyError, TypeError, ValueError):
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.ERROR,
                source="AIOS API",
                detail="Ответ AIOS не соответствует ожидаемому контракту",
            )


def create_aios_status_client(
    environ: Mapping[str, str] | None = None,
) -> AIOSStatusClient:
    """Enable network transport only through an explicit feature flag."""
    values = os.environ if environ is None else environ
    enabled = values.get("AICC_AIOS_STATUS_ENABLED", "").lower() in {"1", "true", "yes"}
    endpoint = values.get("AICC_AIOS_STATUS_URL", "").strip()
    if not enabled or not endpoint:
        return DisabledAIOSStatusClient()
    return HTTPAIOSStatusClient(endpoint)
