"""Read-only AIOS Core status boundary for the desktop control plane."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from collections.abc import Mapping
from ipaddress import ip_address
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 65_536
MAX_ITEMS = 100
MAX_TEXT_LENGTH = 256
CONTRACT_NAME = "aios.core.status"
CONTRACT_VERSION = 1
SAFE_EVIDENCE_KINDS = frozenset({"build", "test", "attestation"})


class NoRedirectHandler(HTTPRedirectHandler):
    """Fail before urllib can create or send a redirected request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "AIOS status redirects are disabled", headers, fp)


def _open_without_redirects(request: Request, *, timeout: float):
    return build_opener(NoRedirectHandler()).open(request, timeout=timeout)


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
        bearer_token: str = "",
        tenant_id: str = "",
        allowed_hosts: frozenset[str] | None = None,
        opener: Callable[..., object] = _open_without_redirects,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._endpoint = endpoint
        self._bearer_token = bearer_token
        self._tenant_id = tenant_id
        self._allowed_hosts = allowed_hosts or frozenset()
        self._opener = opener
        self._timeout_seconds = timeout_seconds

    def get_core_status(self) -> AIOSCoreStatus:
        request = Request(
            self._endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._bearer_token}",
                "X-AIOS-Tenant-ID": self._tenant_id,
            },
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:  # type: ignore[attr-defined]
                final_url = response.geturl() if hasattr(response, "geturl") else self._endpoint
                if not self._is_allowed_url(final_url):
                    raise ValueError("unsafe redirect target")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("response too large")
                payload = json.loads(raw)
        except (TimeoutError, URLError):
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.OFFLINE,
                source="AIOS API",
                detail="Публичный API AIOS Core недоступен",
            )
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.ERROR,
                source="AIOS API",
                detail="Ответ AIOS не соответствует ожидаемому контракту",
            )
        except Exception:
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.ERROR,
                source="AIOS API",
                detail="Не удалось безопасно обработать ответ AIOS",
            )
        try:
            if (
                payload["contract"] != CONTRACT_NAME
                or payload["contract_version"] != CONTRACT_VERSION
                or payload["tenant_id"] != self._tenant_id
            ):
                raise ValueError("contract identity mismatch")
            version = self._bounded_optional_text(payload.get("version"))
            health = self._bounded_optional_text(payload.get("health"))
            capabilities = self._bounded_text_list(payload.get("capabilities", []))
            gates = self._bounded_text_list(payload.get("gates", []))
            evidence = self._safe_evidence(payload.get("evidence", []))
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness(payload["readiness"]),
                source="AIOS API",
                version=version,
                health=health,
                capabilities=capabilities,
                gates=gates,
                evidence=evidence,
            )
        except (KeyError, TypeError, ValueError):
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.ERROR,
                source="AIOS API",
                detail="Ответ AIOS не соответствует ожидаемому контракту",
            )

    def _is_allowed_url(self, value: str) -> bool:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.hostname not in self._allowed_hosts
        ):
            return False
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            return True
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        )

    @staticmethod
    def _bounded_optional_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH:
            raise ValueError("invalid text")
        return value

    @staticmethod
    def _bounded_text_list(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > MAX_ITEMS:
            raise ValueError("invalid list")
        if any(not isinstance(item, str) or len(item) > MAX_TEXT_LENGTH for item in value):
            raise ValueError("invalid list item")
        return tuple(value)

    @staticmethod
    def _safe_evidence(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > MAX_ITEMS:
            raise ValueError("invalid evidence")
        result: list[str] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"kind", "ref"}:
                raise ValueError("invalid evidence")
            kind, reference = item["kind"], item["ref"]
            if (
                kind not in SAFE_EVIDENCE_KINDS
                or not isinstance(reference, str)
                or not reference.isascii()
                or len(reference) > MAX_TEXT_LENGTH
                or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in reference)
            ):
                raise ValueError("unsafe evidence")
            result.append(f"{kind}:{reference}")
        return tuple(result)


def create_aios_status_client(
    environ: Mapping[str, str] | None = None,
) -> AIOSStatusClient:
    """Enable network transport only through an explicit feature flag."""
    values = os.environ if environ is None else environ
    enabled = values.get("AICC_AIOS_STATUS_ENABLED", "").lower() in {"1", "true", "yes"}
    endpoint = values.get("AICC_AIOS_STATUS_URL", "").strip()
    token = values.get("AICC_AIOS_STATUS_TOKEN", "").strip()
    tenant_id = values.get("AICC_AIOS_TENANT_ID", "").strip()
    allowed_hosts = frozenset(
        host.strip().lower()
        for host in values.get("AICC_AIOS_STATUS_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )
    if not enabled or not endpoint or not token or not tenant_id or not allowed_hosts:
        return DisabledAIOSStatusClient()
    client = HTTPAIOSStatusClient(
        endpoint,
        bearer_token=token,
        tenant_id=tenant_id,
        allowed_hosts=allowed_hosts,
    )
    if not client._is_allowed_url(endpoint):
        return DisabledAIOSStatusClient()
    return client
