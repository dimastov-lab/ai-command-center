from command_center.application.aios_status import (
    AIOSCoreReadiness,
    DisabledAIOSStatusClient,
    create_aios_status_client,
)
from command_center.application.aios_tasks import AIOSSDKStatusClient


def test_disabled_status_is_honest_unknown_without_local_fallback():
    status = DisabledAIOSStatusClient().get_core_status()
    assert status.readiness is AIOSCoreReadiness.CONTRACT_PENDING
    assert status.source == "configuration"
    assert status.accepted_sha is None
    assert status.deployed_sha is None
    assert status.evidence == ()


def test_factory_requires_complete_https_tenant_workspace_configuration():
    common = {
        "AICC_AIOS_STATUS_ENABLED": "1",
        "AICC_AIOS_STATUS_TOKEN": "secret",
        "AICC_AIOS_TENANT_ID": "tenant-1",
        "AICC_AIOS_WORKSPACE_ID": "ws-1",
        "AICC_AIOS_STATUS_ALLOWED_HOSTS": "aios.example",
    }
    assert isinstance(create_aios_status_client({}), DisabledAIOSStatusClient)
    assert isinstance(
        create_aios_status_client({**common, "AICC_AIOS_STATUS_URL": "http://bad.test"}),
        DisabledAIOSStatusClient,
    )


def test_factory_builds_only_the_sdk_adapter(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        "command_center.application.aios_tasks.build_aios_status_client",
        lambda **kwargs: sentinel if kwargs["workspace_id"] == "ws-1" else None,
    )
    client = create_aios_status_client(
        {
            "AICC_AIOS_STATUS_ENABLED": "true",
            "AICC_AIOS_STATUS_URL": "https://aios.example",
            "AICC_AIOS_STATUS_TOKEN": "secret",
            "AICC_AIOS_TENANT_ID": "tenant-1",
            "AICC_AIOS_WORKSPACE_ID": "ws-1",
            "AICC_AIOS_STATUS_ALLOWED_HOSTS": "aios.example",
        }
    )
    assert client is sentinel
    assert not isinstance(client, AIOSSDKStatusClient)
