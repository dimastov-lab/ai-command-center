"""The routing matrix (BO-S2a): static, honest, hermetic."""

from __future__ import annotations

from command_center.orchestrator.routing import ROUTING_MATRIX, cascade_for

#: The executors actually proven on the worker fleet. Growing this set is a
#: deliberate edit here, in the same commit that proves the executor exists
#: (the codex link stays a comment until its CLI is verified on worker-01):
#: a phantom link would not fail loudly — the unavailability path advances
#: the cascade, silently burning one attempt of every task's budget.
PROVEN_EXECUTORS = {"claude"}


def test_no_phantom_executors_in_the_matrix():
    for task_class, cascade in ROUTING_MATRIX.items():
        for link in cascade:
            assert link["executor"] in PROVEN_EXECUTORS, (task_class, link)


def test_every_cascade_is_non_empty_and_typed():
    for task_class, cascade in ROUTING_MATRIX.items():
        assert cascade, task_class
        for link in cascade:
            assert isinstance(link.get("executor"), str) and link["executor"]
            assert isinstance(link.get("task_type"), str) and link["task_type"]


def test_cascade_for_returns_copies_not_the_matrix():
    first = cascade_for("review")
    first[0]["executor"] = "mutated"
    assert ROUTING_MATRIX["review"][0]["executor"] == "claude"


def test_unknown_task_class_falls_back_to_implementation():
    assert cascade_for("martian") == cascade_for("implementation")
