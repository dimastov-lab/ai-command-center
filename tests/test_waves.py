"""Wave grouping: read a task's declared parallel group and present the tasks
as launch-in-one-click waves.

The properties that matter are that the label is read from where imports put it,
that grouping is stable and progress-aware, and that the enqueueable set never
includes a task that is done or already queued — because that set is exactly
what the one-click button acts on, and pressing it twice must not double-queue.
"""

from __future__ import annotations

from command_center import execution_queue, waves


def _task(task_id, *, wave=None, meta_wave=None, parallel_group=None,
          status="Backlog", priority="Medium", project="AICC", title=None):
    task = {
        "id": task_id,
        "project": project,
        "title": title or task_id,
        "status": status,
        "priority": priority,
    }
    metadata = {}
    if meta_wave is not None:
        metadata["wave"] = meta_wave
    if parallel_group is not None:
        metadata["parallel_group"] = parallel_group
    if metadata:
        task["metadata"] = metadata
    if wave is not None:
        task["wave"] = wave
    return task


def _entry(task_id, state=execution_queue.STATE_READY):
    return {"id": "q-" + task_id, "task_id": task_id, "state": state}


# --------------------------------------------------------------------------
# Reading the wave label
# --------------------------------------------------------------------------


def test_label_is_read_from_metadata_parallel_group_first():
    task = _task("a", parallel_group="W0", meta_wave="ignored", wave="ignored")
    assert waves.wave_label(task) == "W0"


def test_label_falls_back_to_metadata_wave_then_top_level():
    assert waves.wave_label(_task("a", meta_wave="W1")) == "W1"
    assert waves.wave_label(_task("a", wave="W2")) == "W2"


def test_blank_or_missing_label_is_none():
    assert waves.wave_label(_task("a")) is None
    assert waves.wave_label(_task("a", parallel_group="   ")) is None
    assert waves.wave_label(_task("a", parallel_group=123)) is None


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------


def test_tasks_group_by_wave_in_label_order():
    tasks = [
        _task("a", parallel_group="W1"),
        _task("b", parallel_group="W0"),
        _task("c", parallel_group="W0"),
    ]
    built = waves.build_waves(tasks)
    assert [w.label for w in built] == ["W0", "W1"]
    assert {t.task_id for t in built[0].tasks} == {"b", "c"}


def test_within_a_wave_tasks_are_ordered_by_priority_then_id():
    tasks = [
        _task("low", parallel_group="W0", priority="Low"),
        _task("crit", parallel_group="W0", priority="Critical"),
        _task("high", parallel_group="W0", priority="High"),
    ]
    order = [t.task_id for t in waves.build_waves(tasks)[0].tasks]
    assert order == ["crit", "high", "low"]


def test_unlabelled_tasks_are_omitted():
    tasks = [_task("a", parallel_group="W0"), _task("b")]
    built = waves.build_waves(tasks)
    assert sum(w.total for w in built) == 1


def test_project_filter_uses_canonical_matching():
    tasks = [
        _task("a", parallel_group="W0", project="AI Command Center"),
        _task("b", parallel_group="W0", project="AIOS"),
    ]
    built = waves.build_waves(tasks, project="AICC")
    assert [t.task_id for w in built for t in w.tasks] == ["a"]


# --------------------------------------------------------------------------
# Progress and the enqueueable set
# --------------------------------------------------------------------------


def test_progress_counts_done_and_queued():
    tasks = [
        _task("a", parallel_group="W0", status="Done"),
        _task("b", parallel_group="W0"),
        _task("c", parallel_group="W0"),
    ]
    wave = waves.build_waves(tasks, [_entry("b")])[0]
    assert wave.total == 3
    assert wave.done == 1
    assert wave.queued == 1
    assert wave.enqueueable_ids == ("c",)


def test_a_fully_done_wave_is_complete_and_has_nothing_to_enqueue():
    tasks = [_task("a", parallel_group="W0", status="Done")]
    wave = waves.build_waves(tasks)[0]
    assert wave.complete is True
    assert wave.enqueueable_ids == ()


def test_a_launched_queue_entry_does_not_count_as_still_queued():
    """Only OPEN queue states block re-enqueue; a launched entry means the task
    already ran and is not a candidate for this wave's button anyway."""
    tasks = [_task("a", parallel_group="W0")]
    wave = waves.build_waves(tasks, [_entry("a", state=execution_queue.STATE_LAUNCHED)])[0]
    assert wave.tasks[0].queued is False
    assert wave.enqueueable_ids == ("a",)


def test_enqueueable_excludes_both_done_and_queued():
    tasks = [
        _task("done", parallel_group="W0", status="Done"),
        _task("queued", parallel_group="W0"),
        _task("free", parallel_group="W0"),
    ]
    wave = waves.build_waves(tasks, [_entry("queued")])[0]
    assert set(wave.enqueueable_ids) == {"free"}
