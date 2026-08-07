"""Automatic model selection for task launches.

Picks the cheapest Anthropic model that can reliably handle the task's
complexity. Codex+Ollama is the future local path but requires ``aider``
or a working codex sandbox — use ``select_model_local`` once those are set up.

Priority: cheapest-viable cloud > full cloud default.

  score 0-1  → claude-haiku-4-5-20251001  (cheapest, supports full tool use)
  score 2    → claude-sonnet-4-5           (balanced)
  score 3    → None (caller/provider default, currently sonnet)

The task may override auto-selection by setting a ``model`` field explicitly.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Cloud models (Anthropic)
# ---------------------------------------------------------------------------
HAIKU = "claude-haiku-4-5-20251001"    # cheapest with full tool use
SONNET = "claude-sonnet-4-5"            # balanced
# None → provider default

# ---------------------------------------------------------------------------
# Future local models (Codex+Ollama — requires working sandbox & aider)
# ---------------------------------------------------------------------------
CODEX_OLLAMA_FAST = "ollama/qwen2.5-coder:1.5b"
CODEX_OLLAMA_MED = "ollama/qwen2.5-coder:7b-instruct-q4_K_M"
CODEX_OLLAMA_HEAVY = "ollama/qwen2.5-coder:14b"

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
    Falls back to Haiku for simple tasks and Sonnet for medium ones.
    """
    explicit_model = task.get("model")
    explicit_executor = task.get("executor")

    if explicit_model and explicit_executor:
        return explicit_model, explicit_executor
    if explicit_model:
        executor = "codex" if explicit_model.startswith("ollama/") else "claude_code"
        return explicit_model, executor

    task_type = (task.get("task_type") or "implementation").lower()

    # Read-only / analysis: Ollama text-only (no file editing needed)
    if task_type in ("read_only", "analysis", "research"):
        return CODEX_OLLAMA_MED, "ollama"

    # Implementation: cheapest cloud model that can handle the complexity
    score = _score(task)
    if score <= 1:
        return HAIKU, "claude_code"
    elif score == 2:
        return SONNET, "claude_code"
    else:
        return None, "claude_code"  # provider default
