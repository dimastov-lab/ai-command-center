"""Prompt templates per task type.

A task launched without its own prompt used to reach the agent as nothing but
its title. These tests defend the two rules that make templates safe to add:
an author's deliberate prompt is never overwritten, and a template never
claims a capability the run does not actually grant.
"""

from __future__ import annotations

import pytest

from command_center import agent_runner, prompts


def _task(**overrides):
    task = {"id": "t1", "task_type": "implementation", "title": "Заголовок"}
    task.update(overrides)
    return task


def test_a_deliberate_prompt_is_used_verbatim():
    """Templates fill a gap. Silently replacing an author's instruction with a
    generic one would be worse than having no template at all."""
    assert prompts.build_prompt(_task(prompt="Сделай ровно это")) == "Сделай ровно это"


def test_a_task_without_a_prompt_gets_more_than_its_title():
    """The defect this module exists for: the agent received a bare title with
    no instruction about what to produce or what not to touch."""
    built = prompts.build_prompt(_task(goal="Починить очередь"))
    assert "Починить очередь" in built
    assert len(built) > 200
    assert "Verdict" in built


@pytest.mark.parametrize("task_type", sorted(agent_runner.READ_ONLY_TASK_TYPES))
def test_read_only_types_state_their_boundary(task_type):
    """The run's tool set already denies writing; saying so stops the agent
    wasting a turn discovering it by having a call refused."""
    built = prompts.build_prompt(_task(task_type=task_type, goal="Проверить"))
    assert "только для чтения" in built
    assert prompts.is_read_only(task_type)


@pytest.mark.parametrize("task_type", ["implementation", "remediation"])
def test_writing_types_are_scoped_to_their_worktree(task_type):
    built = prompts.build_prompt(_task(task_type=task_type, goal="Сделать"))
    assert "worktree" in built
    assert "не открывай PR" in built or "не делай push" in built
    assert not prompts.is_read_only(task_type)


def test_an_unknown_task_type_falls_back_without_granting_write():
    """Guessing that an unknown type may write is the dangerous direction."""
    built = prompts.build_prompt(_task(task_type="something_new", goal="Что-то"))
    assert "Что-то" in built
    assert "push" in built and "PR" in built
    assert not prompts.has_template("something_new")


def test_every_template_carries_the_report_contract():
    """`report_parser` parses exactly this shape and the completion pipeline
    reads its verdict — an agent inventing its own format would break the
    machine-readable half of the pipeline."""
    for task_type in ("implementation", "remediation", "review", "architecture_review",
                      "final_gate", "research"):
        built = prompts.build_prompt(_task(task_type=task_type, goal="X"))
        assert "Verdict" in built, task_type
        assert "APPROVED_FOR_COMMIT" in built, task_type


def test_objective_prefers_goal_then_prompt_then_title():
    assert prompts.objective_of({"goal": "Ц", "title": "З"}) == "Ц"
    assert prompts.objective_of({"title": "З"}) == "З"
    assert "отсутствует" in prompts.objective_of({})


def test_read_only_membership_is_read_from_agent_runner_not_duplicated():
    """A second copy of the list would let the stated boundary and the tool set
    the process actually gets drift apart."""
    for task_type in agent_runner.READ_ONLY_TASK_TYPES:
        assert prompts.is_read_only(task_type)
    assert not prompts.is_read_only("implementation")
