import pytest

from command_center import chat_service, models


def test_local_provider_always_available():
    available, _ = chat_service.LocalProvider().is_available()
    assert available is True


def test_local_provider_send_raises_not_implemented():
    provider = chat_service.LocalProvider()
    with pytest.raises(NotImplementedError):
        provider.send(messages=[], project_context="", project_id="AIOS", repository_path=None, timeout_seconds=10)


def test_claude_code_provider_availability_matches_cli_presence():
    available, reason = chat_service.ClaudeCodeChatProvider().is_available()
    assert isinstance(available, bool)
    if not available:
        assert reason


def test_openai_provider_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    available, reason = chat_service.OpenAIChatProvider().is_available()
    assert available is False
    assert "OPENAI_API_KEY" in reason


def test_openai_provider_unavailable_without_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    available, reason = chat_service.OpenAIChatProvider().is_available()
    assert available is False
    assert "OPENAI_MODEL" in reason


def test_openai_provider_unavailable_when_sdk_not_installed(monkeypatch):
    # This environment intentionally does not install the optional `openai` package
    # (requirements.txt stays minimal) — confirms absence never crashes anything,
    # it just reports itself unavailable with a clear reason.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")
    available, reason = chat_service.OpenAIChatProvider().is_available()
    assert available is False
    assert reason


def test_available_providers_always_includes_local_first():
    providers = chat_service.available_providers()
    assert providers[0].name == "local"
    assert {p.name for p in providers} == {"local", "claude_code", "openai"}


def test_get_provider_unknown_name_raises():
    with pytest.raises(ValueError):
        chat_service.get_provider("does-not-exist")


def test_conversation_storage_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_service, "CHATS_FILE", tmp_path / "chats.json")

    conversation = models.new_conversation("AIOS", "Test conversation")
    conversations = [conversation]
    chat_service.save_conversations(conversations)

    loaded = chat_service.load_conversations()
    assert loaded[0]["title"] == "Test conversation"

    message = models.new_message("user", "hello")
    chat_service.append_message(loaded, conversation["id"], message)

    reloaded = chat_service.load_conversations()
    assert reloaded[0]["messages"][0]["content"] == "hello"


def test_append_message_unknown_conversation_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_service, "CHATS_FILE", tmp_path / "chats.json")
    chat_service.save_conversations([])
    with pytest.raises(ValueError):
        chat_service.append_message([], "does-not-exist", models.new_message("user", "x"))
