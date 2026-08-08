"""Tests for model_selector — auto-selection of model and executor per task."""
from __future__ import annotations

import pytest

from command_center import model_selector
from command_center.agent_runner import READ_ONLY_TASK_TYPES


# ---------------------------------------------------------------------------
# Explicit overrides always win
# ---------------------------------------------------------------------------

def test_explicit_model_and_executor_win():
    task = {"model": "my-model", "executor": "my-executor", "task_type": "review"}
    model, executor = model_selector.select_model(task)
    assert model == "my-model"
    assert executor == "my-executor"


def test_explicit_model_without_executor_defaults_to_claude_code():
    task = {"model": "claude-opus-5", "task_type": "review"}
    model, executor = model_selector.select_model(task)
    assert model == "claude-opus-5"
    assert executor == "claude_code"


# ---------------------------------------------------------------------------
# Read-only task types → Ollama (free, local)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_type", sorted(READ_ONLY_TASK_TYPES))
def test_read_only_types_route_to_ollama(task_type):
    task = {"task_type": task_type}
    model, executor = model_selector.select_model(task)
    assert executor == "ollama", (
        f"task_type={task_type!r} should go to ollama (free local), got {executor!r}"
    )
    # Model must be a plain Ollama tag, no "ollama/" prefix (that's Codex format)
    assert not model.startswith("ollama/"), (
        f"Ollama executor expects a plain tag like 'qwen2.5-coder:7b', got {model!r}"
    )


def test_wrong_legacy_types_do_not_route_to_ollama():
    """Names that were incorrectly checked before the fix — should not go to ollama."""
    for wrong_type in ("read_only", "analysis", "research"):
        task = {"task_type": wrong_type}
        _, executor = model_selector.select_model(task)
        assert executor != "ollama", f"legacy type {wrong_type!r} must not route to ollama"


# ---------------------------------------------------------------------------
# Implementation tasks — scored by complexity
# ---------------------------------------------------------------------------

def test_simple_implementation_goes_to_cheap_local_or_haiku():
    """Simple tasks must not go to Sonnet (expensive). Haiku or Aider are both fine."""
    task = {"task_type": "implementation", "title": "add column to table"}
    model, executor = model_selector.select_model(task)
    assert executor in ("aider", "claude_code"), f"got unexpected executor {executor!r}"
    if executor == "claude_code":
        assert model != model_selector.SONNET, "simple task must not use Sonnet"


def test_complex_implementation_uses_cloud():
    """Highly complex tasks should fall back to Anthropic cloud (Haiku or Sonnet)."""
    task = {"task_type": "implementation", "title": "migrate distributed database schema"}
    model, executor = model_selector.select_model(task)
    assert executor == "claude_code", f"complex task should use claude_code, got {executor!r}"


def test_unknown_task_type_does_not_crash():
    task = {"task_type": "something_new_in_the_future"}
    model, executor = model_selector.select_model(task)
    # Any valid executor is fine — just must not raise
    assert executor is not None


def test_missing_task_type_does_not_crash():
    task = {"title": "fix typo in README"}
    model, executor = model_selector.select_model(task)
    assert executor is not None
