from urllib.error import HTTPError, URLError
from urllib.request import Request

from command_center.application.aios_status import (
    AIOSCoreReadiness,
    DisabledAIOSStatusClient,
    HTTPAIOSStatusClient,
    NoRedirectHandler,
    create_aios_status_client,
)


def test_disabled_aios_status_client_reports_pending_contract_without_local_fallback():
    status = DisabledAIOSStatusClient().get_core_status()

    assert status.readiness is AIOSCoreReadiness.CONTRACT_PENDING
    assert status.source == "configuration"
    assert status.version is None
    assert status.capabilities == ()
    assert status.gates == ()
    assert status.evidence == ("Публичный контракт AIOS Core ожидается",)


def test_http_aios_status_client_maps_the_public_read_only_contract():
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            return (
                b'{"contract":"aios.core.status","contract_version":1,'
                b'"tenant_id":"bank-a","version":"0.3.0","health":"healthy","readiness":"ready",'
                b'"capabilities":["memory-api"],"gates":["contract-tests"],'
                b'"evidence":[{"kind":"build","ref":"abc123"}]}'
            )

    client = HTTPAIOSStatusClient(
        "https://aios.invalid/v1/core/status",
        bearer_token="secret",
        tenant_id="bank-a",
        allowed_hosts=frozenset({"aios.invalid"}),
        opener=lambda *_args, **_kwargs: _Response(),
    )

    status = client.get_core_status()

    assert status.readiness is AIOSCoreReadiness.READY
    assert status.source == "AIOS API"
    assert status.version == "0.3.0"
    assert status.health == "healthy"
    assert status.capabilities == ("memory-api",)
    assert status.gates == ("contract-tests",)
    assert status.evidence == ("build:abc123",)


def test_http_aios_status_client_returns_offline_state_without_local_runtime_fallback():
    def _offline(*_args, **_kwargs):
        raise URLError("refused")

    status = HTTPAIOSStatusClient(
        "https://aios.invalid/v1/core/status",
        tenant_id="bank-a",
        allowed_hosts=frozenset({"aios.invalid"}),
        opener=_offline,
    ).get_core_status()

    assert status.readiness is AIOSCoreReadiness.OFFLINE
    assert status.source == "AIOS API"
    assert status.evidence == ()
    assert "недоступен" in (status.detail or "")


def test_transport_is_disabled_unless_feature_flag_and_endpoint_are_both_set():
    assert isinstance(create_aios_status_client({}), DisabledAIOSStatusClient)
    assert isinstance(
        create_aios_status_client(
            {
                "AICC_AIOS_STATUS_ENABLED": "1",
                "AICC_AIOS_STATUS_URL": "https://aios.invalid/v1/core/status",
                "AICC_AIOS_STATUS_TOKEN": "secret",
                "AICC_AIOS_TENANT_ID": "bank-a",
                "AICC_AIOS_STATUS_ALLOWED_HOSTS": "aios.invalid",
            }
        ),
        HTTPAIOSStatusClient,
    )


def test_transport_stays_disabled_without_authentication_and_tenant_binding():
    client = create_aios_status_client(
        {
            "AICC_AIOS_STATUS_ENABLED": "1",
            "AICC_AIOS_STATUS_URL": "https://aios.invalid/v1/core/status",
        }
    )

    assert isinstance(client, DisabledAIOSStatusClient)


def test_http_aios_status_client_reports_invalid_contract_as_error():
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            return b'{"health":"healthy"}'

    status = HTTPAIOSStatusClient(
        "https://aios.invalid/v1/core/status",
        tenant_id="bank-a",
        allowed_hosts=frozenset({"aios.invalid"}),
        opener=lambda *_args, **_kwargs: _Response(),
    ).get_core_status()

    assert status.readiness is AIOSCoreReadiness.ERROR
    assert status.evidence == ()
    assert status.detail == "Ответ AIOS не соответствует ожидаемому контракту"


def test_ready_claim_for_another_tenant_is_rejected():
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            return (
                b'{"contract":"aios.core.status","contract_version":1,'
                b'"tenant_id":"other","readiness":"ready","evidence":[]}'
            )

    status = HTTPAIOSStatusClient(
        "https://aios.invalid/v1/core/status",
        bearer_token="secret",
        tenant_id="bank-a",
        allowed_hosts=frozenset({"aios.invalid"}),
        opener=lambda *_args, **_kwargs: _Response(),
    ).get_core_status()

    assert status.readiness is AIOSCoreReadiness.ERROR


def test_factory_rejects_plain_http_and_unapproved_hosts():
    common = {
        "AICC_AIOS_STATUS_ENABLED": "1",
        "AICC_AIOS_STATUS_TOKEN": "secret",
        "AICC_AIOS_TENANT_ID": "bank-a",
        "AICC_AIOS_STATUS_ALLOWED_HOSTS": "aios.example",
    }

    assert isinstance(
        create_aios_status_client(
            {**common, "AICC_AIOS_STATUS_URL": "http://aios.example/v1/core/status"}
        ),
        DisabledAIOSStatusClient,
    )
    assert isinstance(
        create_aios_status_client(
            {**common, "AICC_AIOS_STATUS_URL": "https://other.example/v1/core/status"}
        ),
        DisabledAIOSStatusClient,
    )


def test_redirect_is_rejected_before_a_second_request_can_forward_credentials():
    handler = NoRedirectHandler()

    try:
        handler.redirect_request(
            req=Request(
                "https://aios.example/v1/core/status",
                headers={"Authorization": "Bearer secret", "X-Aios-Tenant-Id": "bank-a"},
            ),
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="http://169.254.169.254/latest/meta-data",
        )
    except HTTPError as exc:
        assert exc.code == 302
    else:
        raise AssertionError("redirect must be rejected before network I/O")


def test_oversized_or_unexpected_transport_response_is_isolated_as_error():
    class _OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            return b"x" * 65_537

    common = {
        "endpoint": "https://aios.invalid/v1/core/status",
        "tenant_id": "bank-a",
        "allowed_hosts": frozenset({"aios.invalid"}),
    }
    oversized = HTTPAIOSStatusClient(
        **common, opener=lambda *_args, **_kwargs: _OversizedResponse()
    ).get_core_status()

    assert oversized.readiness is AIOSCoreReadiness.ERROR

    broken = HTTPAIOSStatusClient(
        **common,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    ).get_core_status()

    assert broken.readiness is AIOSCoreReadiness.ERROR
