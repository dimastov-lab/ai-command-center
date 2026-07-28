import ast
import inspect

import pytest

from command_center import executors


def test_executors_module_never_constructs_a_git_subprocess_call():
    """Same Automation Safety invariant as `launch.py`: this module is a
    registry and must never itself invoke `git` as a subprocess."""
    source = inspect.getsource(executors)
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "git" not in string_literals


def test_claude_and_codex_are_available_when_codex_probe_succeeds(fake_codex):
    """Availability is a live capability probe, so the exact roster depends on
    what is installed on the host — Ollama, for instance, qualifies wherever it
    is present. Assert the properties that must hold everywhere instead of a
    fixed set that would fail on a well-equipped machine."""
    available = {executor.id for executor in executors.available_executors()}
    assert {"claude_code", "codex"} <= available
    # Executors with no provider behind them can never become available.
    assert available.isdisjoint({"chatgpt", "gemini", "human", "remote_agent"})


def test_all_declared_executors_share_the_same_interface():
    for executor_id in executors.EXECUTOR_IDS:
        executor = executors.get_executor(executor_id)
        assert executor.id == executor_id
        assert executor.label
        assert executor.kind in {"cli", "chat", "human", "remote"}
        assert callable(executor.launch)


def test_get_executor_rejects_unknown_id_and_defaults_only_when_missing():
    with pytest.raises(ValueError, match="Unknown executor"):
        executors.get_executor("nonexistent")
    assert executors.get_executor(None).id == "claude_code"


def test_stub_executors_raise_not_implemented_on_launch():
    for executor_id in ("chatgpt", "gemini", "human", "remote_agent"):
        with pytest.raises(NotImplementedError):
            executors.get_executor(executor_id).launch()


def test_v2_only_executors_fail_closed_on_sync_launch(fake_codex):
    """claude_code, codex, copilot_cli and ollama run only through the
    PID-tracked v2 Execution Center; calling their in-process `launch()`
    directly must fail closed, never silently bypass the Supervisor. (The
    legacy synchronous launch path that ran `claude_code` in-process via
    `agent_runner` was retired — audit MAJOR-3.)"""
    for executor_id in ("claude_code", "codex", "copilot_cli", "ollama"):
        with pytest.raises(RuntimeError, match="PID-tracked Execution Center"):
            executors.get_executor(executor_id).launch()
