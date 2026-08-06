"""Task-based Ollama model selection.

Picks the cheapest locally-available model that handles the task well.
Precedence: explicit model arg → AICC_OLLAMA_MODEL env → task routing → DEFAULT_OLLAMA_MODEL.
"""
from __future__ import annotations

# Maps agent_runner.READ_ONLY_TASK_TYPES → cheapest model that handles each well.
# "review"             — line-by-line diff review: 1.5b is fast and sufficient.
# "final_gate"         — structured pass/fail on code quality: 7b with code tuning.
# "architecture_review"— holistic reasoning across many files: largest available.
TASK_MODEL_MAP: dict[str, str] = {
    "review": "qwen2.5-coder:1.5b",
    "final_gate": "qwen2.5-coder:7b-instruct-q4_K_M",
    "architecture_review": "qwen2.5-coder:14b",
}


def select_model(task_type: str, fallback: str) -> str:
    """Return the best model for *task_type*, or *fallback* if unmapped."""
    return TASK_MODEL_MAP.get(task_type, fallback)
