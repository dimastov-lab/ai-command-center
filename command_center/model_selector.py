"""Automatic model selection for task launches.

Priority: local (free) > cheap cloud > full cloud default.

  read-only types (review, final_gate, architecture_review)
                 → Ollama qwen2.5-coder:7b  (free, local, text-only OK)
  score 0-1      → Aider + qwen2.5-coder:7b (free, local, edits files)
  score 2        → claude-haiku-4-5-20251001  (cheapest Anthropic with tools)
  score 3        → claude-sonnet-4-5           (balanced, complex tasks)

The task may override auto-selection by setting ``model`` and/or ``executor``
fields explicitly.
"""
from __future__ import annotations

from command_center.agent_runner import READ_ONLY_TASK_TYPES

# ---------------------------------------------------------------------------
# Cloud models (Anthropic) — fallback when local isn't enough
# ---------------------------------------------------------------------------
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-5"

# ---------------------------------------------------------------------------
# Local models via Aider + Ollama (free, edits files)
# ---------------------------------------------------------------------------
AIDER_FAST = "ollama/qwen2.5-coder:1.5b"
AIDER_DEFAULT = "ollama/qwen2.5-coder:7b-instruct-q4_K_M"
AIDER_HEAVY = "ollama/qwen2.5-coder:14b"

# ---------------------------------------------------------------------------
# Complexity keywords
# ---------------------------------------------------------------------------
_COMPLEX_KEYWORDS = {
    "architecture", "security", "audit", "migrate", "migration",
    "refactor", "pipeline", "orchestrat", "concurren", "parallel",
    "distributed", "database schema", "breaking change", "bulk", "loop",
}
_SIMPLE_KEYWORDS = {
    "add field", "add column", "rename", "typo", "timestamp", "counter",
    "reset", "fix label", "update label", "increment", "retry counter",
    "metadata row", "completion timestamp",
}


def _score(task: dict) -> int:
    """Complexity score: 0=trivial, 1=simple, 2=medium, 3=complex."""
    text = " ".join([
        task.get("title") or "",
        task.get("description") or "",
        task.get("prompt") or "",
    ]).lower()
    score = 1
    for kw in _COMPLEX_KEYWORDS:
        if kw in text:
            score += 1
            break
    for kw in _SIMPLE_KEYWORDS:
        if kw in text:
            score -= 1
            break
    return max(0, min(score, 3))


def select_model(task: dict) -> tuple[str | None, str]:
    """Return ``(model, executor_id)`` for the given task.

    Explicit ``task["model"]`` and ``task["executor"]`` always win.
    Read-only task types → free local Ollama (text-only, no edits needed).
    Simple/medium implementation → free local Aider + Ollama.
    Complex → cheapest viable Anthropic cloud model.
    """
    explicit_model = task.get("model")
    explicit_executor = task.get("executor")

    if explicit_model and explicit_executor:
        return explicit_model, explicit_executor
    if explicit_model:
        return explicit_model, "claude_code"

    task_type = (task.get("task_type") or "implementation").lower()

    # Read-only: no file editing needed → plain Ollama (free)
    if task_type in READ_ONLY_TASK_TYPES:
        return "qwen2.5-coder:7b-instruct-q4_K_M", "ollama"

    # Implementation: prefer local Aider for simple/medium, cloud for complex
    score = _score(task)
    if score <= 1:
        return AIDER_DEFAULT, "aider"
    elif score == 2:
        return HAIKU, "claude_code"
    else:
        return SONNET, "claude_code"
