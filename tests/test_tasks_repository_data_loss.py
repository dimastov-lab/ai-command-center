"""Regression for the tasks.json data-loss amplification (audit P0/M2).

A transient/torn read of an existing tasks.json used to surface as an empty list
from `load_tasks`; inside `mutate_tasks` that empty list was then mutated and
saved back — overwriting the real store with just the one new record. The
read-modify-write path must instead RAISE on a bad read and persist nothing, so
the on-disk list is never destroyed by a single failed read.
"""
from __future__ import annotations

import json

import pytest

from command_center import tasks_repository as tr


def _seed(root, *titles):
    tr.save_tasks(root, [
        {"id": t.lower(), "title": t, "project": "AICC", "status": "Backlog"} for t in titles
    ])


def test_mutate_tasks_raises_and_preserves_a_corrupt_file(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, "Alpha", "Beta")
    # An existing file that cannot be decoded (torn write / transient error).
    tr.tasks_file_path(root).write_text("{ this is not valid json", encoding="utf-8")
    corrupt = tr.tasks_file_path(root).read_text(encoding="utf-8")

    def _append(tasks):
        tasks.append(tr.new_task_record("AICC", "New", "implementation", "Backlog"))
        return tasks

    with pytest.raises((json.JSONDecodeError, ValueError, OSError)):
        tr.mutate_tasks(root, _append)

    # The file was NOT overwritten with just the new record — nothing persisted.
    assert tr.tasks_file_path(root).read_text(encoding="utf-8") == corrupt


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        '{"tasks": []}',  # valid JSON, but not the list this store holds
        '[{"id": "a"}, ',  # torn write
    ],
)
def test_load_tasks_raises_on_a_corrupt_file_never_returns_empty(isolated_data_dir, content):
    """An unreadable store must surface as an error on EVERY path, including the
    read-only one. Returning `[]` renders an empty board that is indistinguishable
    from "you have no tasks"."""
    root = isolated_data_dir
    _seed(root, "Alpha")
    tr.tasks_file_path(root).write_text(content, encoding="utf-8")

    # Raise-not-`[]` is asserted against broad built-in types, so this fails as
    # "DID NOT RAISE" on the old lenient behavior rather than on a missing symbol.
    with pytest.raises((json.JSONDecodeError, ValueError, OSError)) as excinfo:
        tr.load_tasks(root)
    # The error carries what an operator needs to act: which file, and why.
    assert isinstance(excinfo.value, tr.TasksStoreUnreadable)
    assert excinfo.value.path == tr.tasks_file_path(root)
    assert str(tr.tasks_file_path(root)) in str(excinfo.value)


def test_load_tasks_raises_when_the_file_cannot_be_read(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, "Alpha")
    tasks_file = tr.tasks_file_path(root)
    tasks_file.chmod(0o000)
    try:
        with pytest.raises((json.JSONDecodeError, ValueError, OSError)) as excinfo:
            tr.load_tasks(root)
        assert isinstance(excinfo.value, tr.TasksStoreUnreadable)
    finally:
        tasks_file.chmod(0o644)


def test_mutate_tasks_happy_path_preserves_every_task(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, "Alpha", "Beta")

    def _append(tasks):
        tasks.append(tr.new_task_record("AICC", "Gamma", "implementation", "Backlog"))
        return tasks

    tr.mutate_tasks(root, _append)
    ids = {t["id"] for t in tr.load_tasks(root)}
    assert {"alpha", "beta"} <= ids
    assert len(ids) == 3


def test_missing_file_is_still_a_legitimate_empty_store(isolated_data_dir):
    # A brand-new install has no file yet — that must remain [] (not an error),
    # even on the strict mutation path.
    root = isolated_data_dir
    tr.tasks_file_path(root).unlink(missing_ok=True)

    def _noop(tasks):
        return tasks

    assert tr.mutate_tasks(root, _noop) == []
