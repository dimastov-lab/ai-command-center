"""Regressions for audit BLOCKER-3 (task id uniqueness / delete-all) and
BLOCKER-4 (silent acceptance of records that diverge from the schema/registry).

BLOCKER-3: `delete_task` used to remove *every* record whose id matched, so a
single duplicate id turned one "delete this card" click into a multi-delete.
Two independent guards now prevent that: `load_tasks` drops duplicate ids on
read, and `delete_task` removes at most one record.

BLOCKER-4: `load_tasks` accepted any record silently, so a task whose `project`
was not in `models.PROJECT_IDS` vanished from every project-scoped view with no
signal. `validate_tasks` now surfaces it, and `reconcile_project_aliases`
migrates the known non-canonical labels.
"""
from __future__ import annotations

import types

from command_center import models
from command_center import tasks_repository as tr


def _seed(root, tasks):
    tr.save_tasks(root, tasks)


def test_delete_task_removes_the_task_and_returns_true(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, [
        {"id": "a", "title": "Alpha", "project": "AICC", "status": "Backlog"},
        {"id": "b", "title": "Beta", "project": "AICC", "status": "Backlog"},
    ])
    assert tr.delete_task(root, "a") is True
    assert {t["id"] for t in tr.load_tasks(root)} == {"b"}


def test_delete_task_returns_false_when_absent(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, [{"id": "a", "title": "Alpha", "project": "AICC", "status": "Backlog"}])
    assert tr.delete_task(root, "does-not-exist") is False
    assert {t["id"] for t in tr.load_tasks(root)} == {"a"}


def test_delete_task_removes_only_one_of_duplicate_ids(isolated_data_dir, monkeypatch):
    # BLOCKER-3 core: even if a duplicate id reaches the mutator (here by
    # disabling the read-time dedupe guard), delete_task must remove exactly one
    # record, not both. Bypassing _dedupe_by_id proves delete_task's own guard.
    monkeypatch.setattr(tr, "_dedupe_by_id", lambda tasks: (tasks, []))
    root = isolated_data_dir
    _seed(root, [
        {"id": "dup", "title": "First", "project": "AICC", "status": "Backlog"},
        {"id": "dup", "title": "Second", "project": "AICC", "status": "Backlog"},
    ])
    assert tr.delete_task(root, "dup") is True
    remaining = tr.load_tasks(root)
    assert len(remaining) == 1
    assert remaining[0]["id"] == "dup"


def test_load_tasks_dedupes_duplicate_ids_keeping_first(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, [
        {"id": "dup", "title": "First", "project": "AICC", "status": "Backlog"},
        {"id": "dup", "title": "Second", "project": "AICC", "status": "Backlog"},
        {"id": "other", "title": "Other", "project": "AICC", "status": "Backlog"},
    ])
    loaded = tr.load_tasks(root)
    assert [t["id"] for t in loaded] == ["dup", "other"]
    assert next(t for t in loaded if t["id"] == "dup")["title"] == "First"


def test_dedupe_by_id_reports_dropped_ids():
    tasks = [
        {"id": "a", "title": "A"},
        {"id": "a", "title": "A2"},
        {"id": "b", "title": "B"},
        {"title": "no id kept"},
    ]
    deduped, dropped = tr._dedupe_by_id(tasks)
    assert [t.get("id") for t in deduped] == ["a", "b", None]
    assert dropped == ["a"]


def test_validate_tasks_flags_unknown_project_with_alias_hint():
    issues = tr.validate_tasks([
        {"id": "x", "title": "X", "project": "AI Command Center", "status": "Backlog"},
    ])
    assert len(issues) == 1
    assert "unknown project" in issues[0]
    assert "AICC" in issues[0]  # the alias hint


def test_validate_tasks_flags_missing_required_field():
    issues = tr.validate_tasks([
        {"id": "x", "title": "X", "project": "AICC"},  # no status
    ])
    assert any("missing required field 'status'" in issue for issue in issues)


def test_validate_tasks_flags_duplicate_ids():
    issues = tr.validate_tasks([
        {"id": "x", "title": "A", "project": "AICC", "status": "Backlog"},
        {"id": "x", "title": "B", "project": "AICC", "status": "Backlog"},
    ])
    assert any("duplicate task id" in issue for issue in issues)


def test_validate_tasks_passes_a_clean_list():
    assert tr.validate_tasks([
        {"id": "x", "title": "X", "project": "AICC", "status": "Backlog"},
        {"id": "y", "title": "Y", "project": "AIOS", "status": "Done"},
    ]) == []


def test_create_task_rejects_a_colliding_id(isolated_data_dir, monkeypatch):
    # Force new_task_record to mint the same id twice; the second create must
    # fail closed rather than append a second record with a duplicate id.
    monkeypatch.setattr(tr.uuid, "uuid4", lambda: types.SimpleNamespace(hex="fixed-id"))
    root = isolated_data_dir
    tr.create_task(root, "AICC", "First", "implementation", "Backlog")
    try:
        tr.create_task(root, "AICC", "Second", "implementation", "Backlog")
    except ValueError as exc:
        assert "colliding id" in str(exc)
    else:
        raise AssertionError("expected a colliding-id ValueError")
    assert len(tr.load_tasks(root)) == 1


def test_reconcile_project_aliases_rewrites_and_is_idempotent(isolated_data_dir):
    root = isolated_data_dir
    _seed(root, [
        {"id": "a", "title": "A", "project": "AI Command Center", "status": "Backlog"},
        {"id": "b", "title": "B", "project": "Ecosystem", "status": "Backlog"},
        {"id": "c", "title": "C", "project": "AICC", "status": "Backlog"},
        {"id": "d", "title": "D", "project": "SomethingUnknown", "status": "Backlog"},
    ])
    changed = tr.reconcile_project_aliases(root)
    assert changed == {"AI Command Center": 1, "Ecosystem": 1}

    projects = {t["id"]: t["project"] for t in tr.load_tasks(root)}
    assert projects["a"] == "AICC"
    assert projects["b"] == "ECOSYSTEM"
    assert projects["c"] == "AICC"
    assert projects["d"] == "SomethingUnknown"  # unknown, not a listed alias — left alone

    # Idempotent: a second pass rewrites nothing.
    assert tr.reconcile_project_aliases(root) == {}


def test_reconcile_only_touches_known_aliases_against_the_registry():
    # Guardrail: every alias target must itself be a canonical registry id.
    for canonical in models.PROJECT_ALIASES.values():
        assert canonical in models.PROJECT_IDS
