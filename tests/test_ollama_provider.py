"""The Ollama execution provider.

Ollama differs from Claude Code and Codex in one way that governs the whole
design: its stable non-interactive interface is a plain text completion. It has
no file editing, no shell and no git, so it cannot perform an implementation
task at all. These tests pin that boundary — a provider that quietly accepted
implementation work would produce runs that "succeed" while changing nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import agent_runner, project_config
from command_center.runtime import providers


@pytest.fixture
def provider():
    return providers.get_provider(providers.OLLAMA_ID)


def _spec(provider, **overrides):
    kwargs = dict(
        repository_path=Path("."),
        session_id="s1",
        prompt="review the diff",
        task_type="review",
        is_resume=False,
        model="mistral:latest",
    )
    kwargs.update(overrides)
    return provider.build_launch(**kwargs)


# --------------------------------------------------------------------------
# The capability boundary
# --------------------------------------------------------------------------


def test_implementation_work_is_refused_at_launch(provider):
    """Not a temporary limitation: a run that cannot modify the working tree
    can never satisfy an implementation task."""
    with pytest.raises(ValueError, match="no file-editing or shell"):
        _spec(provider, task_type="implementation")


@pytest.mark.parametrize("task_type", sorted(agent_runner.READ_ONLY_TASK_TYPES))
def test_every_read_only_task_type_is_accepted(provider, task_type):
    assert _spec(provider, task_type=task_type).argv


def test_the_experimental_tool_loop_is_never_enabled(provider):
    """`--experimental` grants tools and `--experimental-yolo` skips every tool
    approval. Neither may appear: that combination is unsupervised write access
    to a repository."""
    argv = _spec(provider).argv
    assert not any(arg.startswith("--experimental") for arg in argv), argv


def test_resume_is_refused_because_each_run_is_stateless(provider):
    with pytest.raises(ValueError, match="resume"):
        _spec(provider, is_resume=True)


# --------------------------------------------------------------------------
# Launch construction
# --------------------------------------------------------------------------


def test_prompt_travels_on_stdin_not_the_command_line(provider):
    """A prompt on argv would leak into the process table and shell history."""
    spec = _spec(provider, prompt="секретный контекст ревью")
    assert spec.stdin_text == "секретный контекст ревью"
    assert not any("секретный" in arg for arg in spec.argv)


def test_output_shaping_flags_protect_the_verdict(provider):
    """Word wrapping corrupts quoted diffs; a reasoning model's thinking is
    noise the verdict must not be parsed out of."""
    argv = _spec(provider).argv
    assert "--nowordwrap" in argv
    assert "--hidethinking" in argv


def test_model_precedence_is_explicit_then_env_then_default(provider, monkeypatch):
    assert _spec(provider, model="gemma4:12b").argv[2] == "gemma4:12b"
    monkeypatch.setenv("AICC_OLLAMA_MODEL", "deepseek-r1:8b")
    assert _spec(provider, model=None).argv[2] == "deepseek-r1:8b"
    monkeypatch.delenv("AICC_OLLAMA_MODEL")
    assert _spec(provider, model=None).argv[2] == providers.DEFAULT_OLLAMA_MODEL


def test_an_oversized_prompt_is_refused_rather_than_silently_truncated(provider):
    with pytest.raises(ValueError, match="limit"):
        provider.validate_prompt("x" * (providers.MAX_OLLAMA_PROMPT_CHARS + 1))


def test_audit_metadata_records_the_sandbox_and_model(provider):
    audit = _spec(provider).audit_metadata
    assert audit["provider_id"] == providers.OLLAMA_ID
    assert audit["sandbox"] == "read_only_no_tools"
    assert audit["model"] == "mistral:latest"


# --------------------------------------------------------------------------
# Availability and failure classification
# --------------------------------------------------------------------------


def test_a_missing_binary_is_reported_not_raised(provider, monkeypatch):
    monkeypatch.setenv("AICC_OLLAMA_BINARY", "/nonexistent/ollama")
    availability = provider.availability()
    assert availability.available is False
    assert availability.code == "executable_missing"


@pytest.mark.parametrize(
    "evidence,expected",
    [
        (["Error: could not connect to ollama app"], "daemon_unreachable"),
        (["Error: model 'x' not found, try pulling it first"], "model_missing"),
        (["cannot allocate memory"], "insufficient_memory"),
        (["something else entirely"], "provider_exit_nonzero"),
    ],
)
def test_failure_evidence_is_classified(provider, evidence, expected):
    assert provider.classify_failure(exit_code=1, diagnostic_lines=evidence) == expected


def test_a_clean_exit_is_not_a_failure(provider):
    assert provider.classify_failure(exit_code=0, diagnostic_lines=[]) is None


# --------------------------------------------------------------------------
# Runtime and authorization
# --------------------------------------------------------------------------


def test_plain_text_lines_become_assistant_events(provider):
    runtime = providers.OllamaRuntime("prompt")
    assert runtime.parse_stdout_line("APPROVED_FOR_COMMIT") == {
        "event_type": "assistant",
        "payload": {"text": "APPROVED_FOR_COMMIT"},
    }
    assert runtime.parse_stdout_line("   \n") is None


def test_identity_verification_is_still_required():
    """Independent of what the process prints: it protects cancellation from
    signalling a reused PID."""
    assert providers.OllamaRuntime("p").requires_verified_identity is True


def test_ollama_is_a_recognized_provider_requiring_explicit_project_opt_in():
    assert providers.OLLAMA_ID in providers.provider_ids()
    assert providers.OLLAMA_ID in project_config.EXECUTION_PROVIDER_IDS
    # Never allowed by default — the legacy-safe default stays Claude-only.
    assert providers.OLLAMA_ID not in project_config.DEFAULT_ALLOWED_AGENTS


# --------------------------------------------------------------------------
# Claude failure classification (reported by the CLI, not inferred)
# --------------------------------------------------------------------------


def _claude():
    return providers.get_provider(providers.CLAUDE_ID)


def test_an_errored_result_becomes_diagnostic_evidence():
    """Without this the CLI's own explanation never reaches
    `classify_failure`, and a run that never reached the model reports only
    a bare exit code."""
    runtime = providers.ClaudeRuntime()
    assert runtime.event_is_provider_error(
        {"event_type": "result", "payload": {"is_error": True, "result": "boom"}}
    )
    assert not runtime.event_is_provider_error(
        {"event_type": "result", "payload": {"is_error": False}}
    )
    assert not runtime.event_is_provider_error({"event_type": "assistant", "payload": {}})


@pytest.mark.parametrize(
    "evidence,expected",
    [
        # The real payload observed from an expired local session.
        ("Failed to authenticate: OAuth session expired and could not be refreshed", "session_expired"),
        ("Please run /login to continue", "session_expired"),
        ("usage limit reached; check your quota", "quota_limit"),
        ("unauthorized: invalid api key", "authentication_failed"),
        ("Upstream overloaded, try again", "provider_api_error"),
        ("something unrecognized", "provider_exit_nonzero"),
    ],
)
def test_claude_failures_are_named_from_cli_evidence(evidence, expected):
    assert _claude().classify_failure(exit_code=1, diagnostic_lines=[evidence]) == expected


def test_session_expiry_is_not_conflated_with_a_quota_limit():
    """Both read as 'authentication-adjacent' in wording, but they send the
    operator to different remedies."""
    assert _claude().classify_failure(
        exit_code=1, diagnostic_lines=["OAuth session expired"]
    ) != _claude().classify_failure(exit_code=1, diagnostic_lines=["spend limit exceeded"])


def test_a_clean_claude_exit_is_not_a_failure():
    assert _claude().classify_failure(exit_code=0, diagnostic_lines=[]) is None


def test_every_provider_failure_code_has_operator_remediation():
    from command_center import task_pipeline

    codes = set()
    for provider in (providers.get_provider(pid) for pid in providers.provider_ids()):
        for evidence in (
            "OAuth session expired", "usage limit", "unauthorized", "overloaded",
            "could not connect to ollama app", "model 'x' not found", "cannot allocate memory", "?",
        ):
            code = provider.classify_failure(exit_code=1, diagnostic_lines=[evidence])
            if code:
                codes.add(code)
    missing = codes - set(task_pipeline.REMEDIATION_BY_REASON)
    assert not missing, f"нет подсказки для: {sorted(missing)}"
