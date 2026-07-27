import ast
import inspect

import pytest

from command_center import executors


def test_executors_module_never_constructs_a_git_subprocess_call():
    """Same Automation Safety invariant as `launch.py`: this module must
    never itself invoke `git` as a subprocess (the `claude_code` executor
    delegates to `agent_runner`, which enforces its own git-write denylist
    independently)."""
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


def test_codex_sync_launch_fails_closed_instead_of_bypassing_supervisor(fake_codex):
    with pytest.raises(RuntimeError, match="PID-tracked Execution Center"):
        executors.get_executor("codex").launch()
