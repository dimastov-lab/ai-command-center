from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from command_center.application import aios_tasks
from command_center.application.aios_status import (
    AIOSCoreReadiness,
    AIOSStatusAuthenticationError,
    AIOSStatusAuthorizationError,
    AIOSStatusContractError,
    AIOSStatusRemoteError,
    AIOSStatusTenantError,
    AIOSStatusTimeoutError,
)
from command_center.application.aios_tasks import (
    AIOSSDKStatusClient,
    validate_aios_sdk_contract,
)


def _response(data, request_id: str):
    return SimpleNamespace(data=data, request_id=request_id)


def _page(items, request_id: str):
    return SimpleNamespace(items=tuple(items), request_id=request_id)


def _contract():
    return SimpleNamespace(
        distribution="aios-sdk",
        sdk_version="0.2.0",
        api_major=1,
        callable_operations=frozenset(
            {"getHealth", "getReadiness", "whoAmI", "listWorkspaceTimeline"}
        ),
        core_status_sources={
            "liveness": "getHealth",
            "readiness": "getReadiness",
            "identity_and_capabilities": "whoAmI",
            "workspace_activity": "listWorkspaceTimeline",
        },
        unknown_evidence=("accepted_sha", "deployed_sha"),
    )


class _SDKError(Exception):
    request_id = None
    code = None


class _AuthenticationError(_SDKError):
    pass


class _AuthorizationError(_SDKError):
    pass


class _TimeoutError(_SDKError):
    pass


class _ContractError(_SDKError):
    pass


def _sdk():
    return SimpleNamespace(
        __version__="0.2.0",
        SUPPORTED_API_MAJOR=1,
        SDK_CONTRACT=_contract(),
        AIOSSDKError=_SDKError,
        AuthenticationError=_AuthenticationError,
        AuthorizationError=_AuthorizationError,
        TimeoutError=_TimeoutError,
        ContractError=_ContractError,
    )


def _client(*, tenant_id: str = "tenant-1", timeline_event=None):
    event = timeline_event or SimpleNamespace(
        id="evt-1",
        event_type="workspace_updated",
        occurred_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        actor_principal_id="principal-secret",
        payload={"prompt": "must-not-leak", "local_path": "/private/worktree"},
    )
    return SimpleNamespace(
        health=SimpleNamespace(
            get=lambda: _response(
                SimpleNamespace(status="ok", build_version="build-1"), "req-health"
            )
        ),
        readiness=SimpleNamespace(
            get=lambda: _response(
                SimpleNamespace(status="ready", build_version="build-1"),
                "req-readiness",
            )
        ),
        identity=SimpleNamespace(
            who_am_i=lambda: _response(
                SimpleNamespace(
                    tenant_id=tenant_id,
                    principal_id="principal-secret",
                    capabilities=("tasks:read", "timeline:read"),
                ),
                "req-whoami",
            )
        ),
        workspaces=SimpleNamespace(
            list_timeline=lambda *_args, **_kwargs: _page([event], "req-timeline")
        ),
        close=lambda: None,
    )


def test_installed_sdk_contract_is_exact_and_supported_without_skip():
    sdk = aios_tasks._load_aios_sdk()
    validate_aios_sdk_contract(sdk)
    assert sdk.__version__ == "0.2.0"
    assert sdk.SDK_CONTRACT.unknown_evidence == ("accepted_sha", "deployed_sha")


def test_status_adapter_composes_supported_sources_and_redacts_timeline():
    status = AIOSSDKStatusClient(
        _client(), tenant_id="tenant-1", workspace_id="ws-1", sdk=_sdk()
    ).get_core_status()

    assert status.readiness is AIOSCoreReadiness.READY
    assert status.version == "build-1"
    assert status.health == "ok"
    assert status.tenant_id == "tenant-1"
    assert status.capabilities == ("tasks:read", "timeline:read")
    assert status.timeline[0].as_dict() == {
        "event_id": "evt-1",
        "event_type": "workspace_updated",
        "occurred_at": "2026-08-09T00:00:00+00:00",
    }
    rendered = repr(status)
    assert "must-not-leak" not in rendered
    assert "principal-secret" not in rendered
    assert "/private/worktree" not in rendered
    assert [item.as_dict() for item in status.evidence] == [
        {"event": "health.get", "request_id": "req-health"},
        {"event": "readiness.get", "request_id": "req-readiness"},
        {"event": "identity.who_am_i", "request_id": "req-whoami"},
        {"event": "workspaces.list_timeline", "request_id": "req-timeline"},
    ]
    assert status.accepted_sha is None
    assert status.deployed_sha is None


def test_wrong_tenant_fails_closed_with_typed_error():
    client = AIOSSDKStatusClient(
        _client(tenant_id="other"), tenant_id="tenant-1", workspace_id="ws-1", sdk=_sdk()
    )
    with pytest.raises(AIOSStatusTenantError):
        client.get_core_status()


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        (_AuthenticationError, AIOSStatusAuthenticationError),
        (_AuthorizationError, AIOSStatusAuthorizationError),
        (_TimeoutError, AIOSStatusTimeoutError),
        (_ContractError, AIOSStatusContractError),
        (_SDKError, AIOSStatusRemoteError),
    ],
)
def test_sdk_failures_are_typed_and_do_not_leak_raw_messages(sdk_error, expected):
    client = _client()

    def fail():
        raise sdk_error("credential and raw body must-not-leak")

    client.identity.who_am_i = fail
    adapter = AIOSSDKStatusClient(
        client, tenant_id="tenant-1", workspace_id="ws-1", sdk=_sdk()
    )
    with pytest.raises(expected) as caught:
        adapter.get_core_status()
    assert "must-not-leak" not in str(caught.value)


def test_malformed_timeline_event_fails_closed():
    malformed = SimpleNamespace(
        id="evt-1",
        event_type={"unexpected": "mapping"},
        occurred_at=datetime.now(timezone.utc),
        payload={},
    )
    adapter = AIOSSDKStatusClient(
        _client(timeline_event=malformed),
        tenant_id="tenant-1",
        workspace_id="ws-1",
        sdk=_sdk(),
    )
    with pytest.raises(AIOSStatusContractError):
        adapter.get_core_status()


def test_sync_async_sdk_identity_and_timeline_parity():
    sdk = aios_tasks._load_aios_sdk()

    def payload(path: str):
        if path == "/api/v1/whoami":
            return {
                "data": {
                    "principal_id": "principal-1",
                    "tenant_id": "tenant-1",
                    "capabilities": ["timeline:read"],
                },
                "meta": {"request_id": "req-identity"},
            }
        return {
            "data": [
                {
                    "id": "evt-1",
                    "event_type": "workspace_updated",
                    "actor_principal_id": "principal-1",
                    "occurred_at": "2026-08-09T00:00:00Z",
                    "payload": {},
                    "scope": {"type": "workspace", "workspace_id": "ws-1"},
                    "subject": {
                        "entity_id": "ws-1",
                        "entity_type": "workspace",
                        "scope": {"type": "workspace", "workspace_id": "ws-1"},
                    },
                }
            ],
            "page": {"next_cursor": None, "has_more": False},
            "meta": {"request_id": "req-timeline"},
        }

    def response(request: httpx.Request):
        request_id = "req-identity" if request.url.path.endswith("whoami") else "req-timeline"
        return httpx.Response(
            200,
            json=payload(request.url.path),
            headers={"X-Request-ID": request_id},
        )

    with sdk.AIOSClient(
        "https://example.test", token="secret", transport=httpx.MockTransport(response)
    ) as sync_client:
        sync_identity = sync_client.identity.who_am_i().data
        sync_timeline = sync_client.workspaces.list_timeline("ws-1").items

    async def async_values():
        async with sdk.AsyncAIOSClient(
            "https://example.test", token="secret", transport=httpx.MockTransport(response)
        ) as async_client:
            identity = (await async_client.identity.who_am_i()).data
            timeline = (await async_client.workspaces.list_timeline("ws-1")).items
            return identity, timeline

    async_identity, async_timeline = asyncio.run(async_values())
    assert async_identity == sync_identity
    assert async_timeline == sync_timeline
