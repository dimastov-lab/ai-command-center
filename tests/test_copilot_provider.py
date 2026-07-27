"""Tests for the GitHub Copilot CLI execution provider."""

from __future__ import annotations

import json

import pytest

from command_center.runtime import providers


def test_copilot_id_and_label():
    provider = providers.get_provider("copilot_cli")
    assert provider.id == "copilot_cli"
    assert provider.label == "Copilot CLI"
    assert provider.supports_resume is False
    assert provider.requires_dedicated_worktree is True


def test_copilot_discovery_and_version_probe_use_fake_only(fake_copilot):
    availability = providers.get_provider("copilot_cli").availability()
    assert availability.available is True
    assert availability.code == "usable"
    assert availability.executable == str(fake_copilot)
    assert "fake" in (availability.version or "").lower()


def test_copilot_missing_executable_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("AICC_COPILOT_BINARY", str(tmp_path / "missing"))
    availability = providers.get_provider("copilot_cli").availability()
    assert availability.available is False
    assert availability.code == "executable_missing"
    assert "install" in availability.message.lower() or "configure" in availability.message.lower()


def test_copilot_version_probe_failure_is_distinct(fake_copilot, monkeypatch):
    monkeypatch.setattr(
        providers,
        "_probe",
        lambda executable, args, *, provider_id: (False, "copilot version probe failed (exit 9)"),
    )
    availability = providers.get_provider("copilot_cli").availability()
    assert availability.available is False
    assert availability.code == "version_probe_failed"


def test_copilot_argv_is_fixed_and_prompt_is_stdin_only(fake_copilot, git_repo):
    prompt = "secret; $(whoami); rm -rf /"
    spec = providers.get_provider("copilot_cli").build_launch(
        repository_path=git_repo,
        session_id="unused",
        prompt=prompt,
        task_type="implementation",
        is_resume=False,
        model=None,
    )
    assert spec.argv[0] == str(fake_copilot)
    assert "--output-format" in spec.argv
    assert "json" in spec.argv
    assert "--no-color" in spec.argv
    assert "--allow-all-tools" in spec.argv
    assert "-C" in spec.argv
    assert str(git_repo) in spec.argv
    assert prompt not in spec.argv
    assert spec.stdin_text == prompt
    assert spec.audit_metadata["prompt_transport"] == "stdin"
    assert "secret" not in json.dumps(spec.audit_metadata)


def test_copilot_model_is_passed_through_argv(fake_copilot, git_repo):
    spec = providers.get_provider("copilot_cli").build_launch(
        repository_path=git_repo,
        session_id="unused",
        prompt="do something",
        task_type="implementation",
        is_resume=False,
        model="gpt-5.4",
    )
    assert "--model" in spec.argv
    assert "gpt-5.4" in spec.argv


def test_copilot_resume_is_rejected(fake_copilot, git_repo):
    with pytest.raises(ValueError, match="resume"):
        providers.get_provider("copilot_cli").build_launch(
            repository_path=git_repo,
            session_id="unused",
            prompt="do something",
            task_type="implementation",
            is_resume=True,
            model=None,
        )


def test_copilot_prompt_limit_enforced():
    with pytest.raises(ValueError, match="safety limit"):
        providers.get_provider("copilot_cli").validate_prompt("x" * (providers.MAX_COPILOT_PROMPT_CHARS + 1))


def test_copilot_runtime_parses_turn_start_as_readiness():
    runtime = providers.CopilotRuntime("prompt")
    line = json.dumps({"type": "assistant.turn_start", "data": {"turnId": "0"}})
    event = runtime.parse_stdout_line(line)
    assert event is not None
    assert event["event_type"] == "lifecycle"
    assert runtime.stdout_event_is_readiness(line, event) is True


def test_copilot_runtime_parses_assistant_message():
    runtime = providers.CopilotRuntime("prompt")
    line = json.dumps({
        "type": "assistant.message",
        "data": {"content": "Hello, world!", "model": "claude-sonnet-5", "toolRequests": []},
    })
    event = runtime.parse_stdout_line(line)
    assert event is not None
    assert event["event_type"] == "assistant_message"
    assert "Hello, world!" in event["payload"]["message"]["content"][0]["text"]


def test_copilot_runtime_parses_turn_end_as_valid_result():
    runtime = providers.CopilotRuntime("prompt")
    # Feed an assistant message first to populate the text
    msg_line = json.dumps({"type": "assistant.message", "data": {"content": "done"}})
    runtime.parse_stdout_line(msg_line)
    # Then the turn end
    end_line = json.dumps({"type": "assistant.turn_end", "data": {"turnId": "0"}})
    event = runtime.parse_stdout_line(end_line)
    assert event is not None
    assert event["event_type"] == "result"
    assert event["payload"]["provider_completion_valid"] is True
    assert event["payload"]["result"] == "done"
    assert providers.CopilotRuntime.event_is_valid_result(event) is True


def test_copilot_runtime_turn_end_without_message_is_not_valid():
    runtime = providers.CopilotRuntime("prompt")
    line = json.dumps({"type": "assistant.turn_end", "data": {"turnId": "0"}})
    event = runtime.parse_stdout_line(line)
    assert event is not None
    assert event["event_type"] == "result"
    assert event["payload"]["provider_completion_valid"] is False
    assert providers.CopilotRuntime.event_is_valid_result(event) is False


def test_copilot_runtime_parses_nonzero_result_as_provider_error():
    runtime = providers.CopilotRuntime("prompt")
    line = json.dumps({"type": "result", "exitCode": 1, "usage": {}})
    event = runtime.parse_stdout_line(line)
    assert event is not None
    assert event["event_type"] == "provider_error"
    assert providers.CopilotRuntime.event_is_provider_error(event) is True


def test_copilot_runtime_parses_error_event_as_provider_error():
    runtime = providers.CopilotRuntime("prompt")
    line = json.dumps({"type": "error", "data": {"message": "something broke"}})
    event = runtime.parse_stdout_line(line)
    assert event is not None
    assert event["event_type"] == "provider_error"
    assert "something broke" in event["payload"]["message"]


def test_copilot_runtime_ignores_unknown_event_types():
    runtime = providers.CopilotRuntime("prompt")
    line = json.dumps({"type": "session.tools_updated", "data": {"model": "claude-sonnet-5"}})
    event = runtime.parse_stdout_line(line)
    assert event is None


def test_copilot_runtime_ignores_non_json_lines():
    runtime = providers.CopilotRuntime("prompt")
    assert runtime.parse_stdout_line("not json {{{") is None
    assert runtime.parse_stdout_line("") is None


def test_copilot_runtime_requires_valid_result():
    assert providers.CopilotRuntime.requires_valid_result is True


def test_copilot_runtime_requires_verified_identity():
    assert providers.CopilotRuntime.requires_verified_identity is True


def test_copilot_classify_failure_quota():
    result = providers.CopilotProvider.classify_failure(
        exit_code=1, diagnostic_lines=["AI credit usage limit reached"],
    )
    assert result == "quota_limit"


def test_copilot_classify_failure_auth():
    result = providers.CopilotProvider.classify_failure(
        exit_code=1, diagnostic_lines=["not logged in; authentication required"],
    )
    assert result == "authentication_failed"


def test_copilot_classify_failure_network():
    result = providers.CopilotProvider.classify_failure(
        exit_code=1, diagnostic_lines=["ECONNREFUSED connection refused"],
    )
    assert result == "network_error"


def test_copilot_classify_failure_generic_nonzero():
    result = providers.CopilotProvider.classify_failure(
        exit_code=1, diagnostic_lines=["unexpected error"],
    )
    assert result == "provider_exit_nonzero"


def test_copilot_classify_failure_zero_exit_is_none():
    result = providers.CopilotProvider.classify_failure(
        exit_code=0, diagnostic_lines=[""],
    )
    assert result is None


def test_copilot_sanitizes_prompt_echo_in_user_message():
    secret_prompt = "my-secret-api-key-12345678"
    runtime = providers.CopilotRuntime(secret_prompt)
    # The user.message event echoes the prompt content
    line = json.dumps({"type": "user.message", "data": {"content": secret_prompt}})
    event = runtime.parse_stdout_line(line)
    # user.message is not a recognized event type — ignored
    assert event is None


def test_copilot_provider_registered_in_providers_dict():
    assert "copilot_cli" in providers.provider_ids()


def test_copilot_executor_registered():
    from command_center import executors
    assert "copilot_cli" in executors.EXECUTORS
    executor = executors.get_executor("copilot_cli")
    assert executor.label == "Copilot CLI"
    assert executor.kind == "cli"
    assert executor.supports_terminal_launch is True


def test_copilot_in_execution_provider_ids():
    from command_center import project_config
    assert "copilot_cli" in project_config.EXECUTION_PROVIDER_IDS