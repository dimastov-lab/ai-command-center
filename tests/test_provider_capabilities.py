from command_center.application.provider_capabilities import (
    CompatibilityProviderCapabilityClient,
    DisabledProviderCapabilityClient,
    ProviderReadiness,
    create_provider_capability_client,
)


def test_binary_present_with_unknown_auth_is_not_reported_ready():
    client = CompatibilityProviderCapabilityClient(
        which=lambda name: f"/usr/local/bin/{name}",
        ollama_probe=lambda: None,
    )

    statuses = {item.provider_id: item for item in client.list_capabilities()}

    assert statuses["antigravity"].readiness is ProviderReadiness.LOGIN_REQUIRED
    assert statuses["antigravity"].provenance == "локальный исполняемый файл"
    assert statuses["opencode"].readiness is ProviderReadiness.LOGIN_REQUIRED
    assert statuses["claude"].readiness is ProviderReadiness.AUTH_UNKNOWN


def test_ollama_distinguishes_daemon_unavailable_from_available_models():
    unavailable = CompatibilityProviderCapabilityClient(
        which=lambda name: "/opt/homebrew/bin/ollama" if name == "ollama" else None,
        ollama_probe=lambda: None,
    )
    available = CompatibilityProviderCapabilityClient(
        which=lambda name: "/opt/homebrew/bin/ollama" if name == "ollama" else None,
        ollama_probe=lambda: ("qwen3", "gemma3"),
    )

    down = {item.provider_id: item for item in unavailable.list_capabilities()}["ollama"]
    up = {item.provider_id: item for item in available.list_capabilities()}["ollama"]
    assert down.readiness is ProviderReadiness.DAEMON_UNAVAILABLE
    assert up.readiness is ProviderReadiness.AVAILABLE
    assert up.detail == "Доступно моделей: 2"


def test_compatibility_probe_is_behind_an_explicit_feature_flag():
    assert isinstance(create_provider_capability_client({}), DisabledProviderCapabilityClient)
    assert isinstance(
        create_provider_capability_client({"AICC_PROVIDER_STATUS_ENABLED": "1"}),
        CompatibilityProviderCapabilityClient,
    )
