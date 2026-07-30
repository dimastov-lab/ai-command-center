from urllib.error import URLError

from command_center.application.aios_status import (
    AIOSCoreReadiness,
    DisabledAIOSStatusClient,
    HTTPAIOSStatusClient,
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

        def read(self):
            return (
                b'{"version":"0.3.0","health":"healthy","readiness":"ready",'
                b'"capabilities":["memory-api"],"gates":["contract-tests"],'
                b'"evidence":["build:abc123"]}'
            )

    client = HTTPAIOSStatusClient(
        "https://aios.invalid/v1/core/status",
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
        "https://aios.invalid/v1/core/status", opener=_offline
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
            }
        ),
        HTTPAIOSStatusClient,
    )


def test_http_aios_status_client_reports_invalid_contract_as_error():
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"health":"healthy"}'

    status = HTTPAIOSStatusClient(
        "https://aios.invalid/v1/core/status",
        opener=lambda *_args, **_kwargs: _Response(),
    ).get_core_status()

    assert status.readiness is AIOSCoreReadiness.ERROR
    assert status.evidence == ()
    assert status.detail == "Ответ AIOS не соответствует ожидаемому контракту"
