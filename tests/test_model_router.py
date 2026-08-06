"""Unit tests for task-based Ollama model routing."""
from __future__ import annotations

from command_center import model_router
from command_center.agent_runner import READ_ONLY_TASK_TYPES


def test_every_read_only_task_type_has_a_mapping():
    for task_type in READ_ONLY_TASK_TYPES:
        model = model_router.select_model(task_type, fallback="fallback")
        assert model != "fallback", f"{task_type!r} not in TASK_MODEL_MAP"


def test_unknown_task_type_returns_fallback():
    assert model_router.select_model("unknown_task", fallback="my-fallback") == "my-fallback"


def test_review_is_the_cheapest_model():
    review = model_router.TASK_MODEL_MAP["review"]
    others = [v for k, v in model_router.TASK_MODEL_MAP.items() if k != "review"]
    # Review model should be a smaller/faster variant (1.5b vs 7b/14b).
    assert "1.5b" in review or "3b" in review, f"Expected a small model for review, got {review!r}"
    for other in others:
        assert review != other


def test_architecture_review_uses_largest_model():
    arch = model_router.TASK_MODEL_MAP["architecture_review"]
    review = model_router.TASK_MODEL_MAP["review"]
    gate = model_router.TASK_MODEL_MAP["final_gate"]
    # Architecture review should use a larger model than plain review and final_gate.
    assert arch != review
    assert arch != gate
