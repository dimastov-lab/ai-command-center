"""Multi-format task-package import (AICC-D1-002): YAML, Markdown and plain
text feed the *same* validate/preview/apply pipeline as JSON, so a package is
identical however it was authored. JSON coverage lives in
`tests/test_task_import.py`; this module covers the added formats, the format
resolution rules, and the atomic-persistence rollback guarantee.
"""

from __future__ import annotations

import json

import pytest

from command_center import task_import as ti, tasks_repository


def _task(**overrides) -> dict:
    base = {
        "id": "PKG-001",
        "title": "Do the thing",
        "goal": "Do the thing in detail",
        "prompt": "Do it.",
        "repository_path": "/repo",
        "workspace_path": "/repo",
        "branch": "main",
        "status": "Backlog",
        "project": "AIOS",
        "task_type": "implementation",
        "priority": "Medium",
        "depends_on": [],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# YAML
# --------------------------------------------------------------------------


def test_parse_yaml_list_by_extension_produces_a_legacy_package():
    yaml = pytest.importorskip("yaml")
    text = yaml.safe_dump([_task(id="PKG-Y1")])
    parsed = ti.parse_task_package(text, filename="wave.yaml")
    assert parsed.schema_version == "legacy"
    assert parsed.package_id.startswith("legacy-")
    assert [t["id"] for t in parsed.tasks] == ["PKG-Y1"]


def test_parse_yaml_envelope_by_yml_extension_uses_given_id_and_schema():
    yaml = pytest.importorskip("yaml")
    payload = {"schema_version": "1", "package_id": "wave-yaml", "tasks": [_task(id="PKG-Y1")]}
    parsed = ti.parse_task_package(yaml.safe_dump(payload), filename="wave.yml")
    assert parsed.schema_version == "1"
    assert parsed.package_id == "wave-yaml"


def test_parse_yaml_block_style_validates_like_json():
    pytest.importorskip("yaml")
    text = (
        "- id: PKG-Y1\n"
        "  title: YAML task\n"
        "  goal: Do the YAML thing\n"
        "  prompt: Do it.\n"
        "  repository_path: /repo\n"
        "  workspace_path: /repo\n"
        "  branch: main\n"
        "  status: Backlog\n"
        "  project: AIOS\n"
        "  task_type: implementation\n"
        "  priority: Medium\n"
        "  depends_on: []\n"
    )
    parsed = ti.parse_task_package(text, filename="wave.yaml")
    validation = ti.validate_task_package(parsed)
    assert validation.errors == []
    assert validation.items[0]["id"] == "PKG-Y1"
    assert validation.items[0]["project"] == "AIOS"


def test_parse_malformed_yaml_raises_task_import_error():
    pytest.importorskip("yaml")
    with pytest.raises(ti.TaskImportError):
        ti.parse_task_package("- id: [unterminated\n", filename="bad.yaml")


def test_parse_fmt_override_beats_extension():
    yaml = pytest.importorskip("yaml")
    # A .json extension, but fmt="yaml" forces the YAML parser on block YAML
    # that json.loads could never read.
    text = yaml.safe_dump([_task(id="PKG-Y1")])
    parsed = ti.parse_task_package(text, filename="misnamed.json", fmt="yaml")
    assert [t["id"] for t in parsed.tasks] == ["PKG-Y1"]


def test_parse_unknown_fmt_raises():
    with pytest.raises(ti.TaskImportError):
        ti.parse_task_package("[]", fmt="xml")


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def test_parse_markdown_yaml_front_matter():
    yaml = pytest.importorskip("yaml")
    body = yaml.safe_dump([_task(id="PKG-M1")])
    text = f"---\n{body}---\n\n# Founder audit report\n\nSome human-readable prose.\n"
    parsed = ti.parse_task_package(text, filename="report.md")
    assert [t["id"] for t in parsed.tasks] == ["PKG-M1"]


def test_parse_markdown_fenced_json_block_needs_no_yaml():
    payload = {"schema_version": "1", "package_id": "md-json", "tasks": [_task(id="PKG-M1")]}
    text = (
        "# Report\n\nIntro prose.\n\n"
        "```json\n" + json.dumps(payload) + "\n```\n\nTrailing prose.\n"
    )
    parsed = ti.parse_task_package(text, filename="report.markdown")
    assert parsed.package_id == "md-json"
    assert [t["id"] for t in parsed.tasks] == ["PKG-M1"]


def test_parse_markdown_fenced_yaml_block():
    yaml = pytest.importorskip("yaml")
    body = yaml.safe_dump([_task(id="PKG-M1")])
    text = f"# Report\n\n```yaml\n{body}```\n"
    parsed = ti.parse_task_package(text, filename="report.md")
    assert [t["id"] for t in parsed.tasks] == ["PKG-M1"]


def test_parse_markdown_without_any_structured_block_raises():
    text = "# Just a heading\n\nNothing structured here, only prose.\n"
    with pytest.raises(ti.TaskImportError, match="Markdown"):
        ti.parse_task_package(text, filename="report.md")


def test_parse_markdown_front_matter_wins_over_a_later_fence():
    yaml = pytest.importorskip("yaml")
    body = yaml.safe_dump([_task(id="FRONT")])
    later = json.dumps([_task(id="FENCE")])
    text = f"---\n{body}---\n\n```json\n{later}\n```\n"
    parsed = ti.parse_task_package(text, filename="report.md")
    assert [t["id"] for t in parsed.tasks] == ["FRONT"]


# --------------------------------------------------------------------------
# Plain text + sniffing / backward compatibility
# --------------------------------------------------------------------------


def test_parse_text_file_containing_json():
    text = json.dumps([_task(id="PKG-T1")])
    parsed = ti.parse_task_package(text, filename="tasks.txt")
    assert [t["id"] for t in parsed.tasks] == ["PKG-T1"]


def test_parse_text_file_containing_yaml():
    yaml = pytest.importorskip("yaml")
    text = yaml.safe_dump([_task(id="PKG-T1")])
    parsed = ti.parse_task_package(text, filename="tasks.txt")
    assert [t["id"] for t in parsed.tasks] == ["PKG-T1"]


def test_parse_without_filename_still_sniffs_json_for_backward_compat():
    parsed = ti.parse_task_package(json.dumps([_task(id="PKG-B1")]))
    assert parsed.schema_version == "legacy"
    assert [t["id"] for t in parsed.tasks] == ["PKG-B1"]


def test_hash_matches_json_pipeline_for_identical_bytes():
    text = json.dumps([_task(id="PKG-1")])
    # Same bytes, routed as JSON and as text — hash is over raw bytes, so both
    # dedupe to the same package hash/id regardless of the chosen parser.
    as_json = ti.parse_task_package(text, filename="a.json")
    as_text = ti.parse_task_package(text, filename="a.txt")
    assert as_json.package_hash == as_text.package_hash
    assert as_json.package_id == as_text.package_id


# --------------------------------------------------------------------------
# Cross-format equivalence + full pipeline
# --------------------------------------------------------------------------


def test_yaml_and_json_packages_apply_to_equivalent_records(tmp_path):
    yaml = pytest.importorskip("yaml")
    tasks = [_task(id="PKG-EQ", source="Founder Audit", parallel_group="WAVE-1")]

    json_parsed = ti.parse_task_package(json.dumps(tasks), filename="p.json")
    json_validation = ti.validate_task_package(json_parsed)
    ti.apply_task_package(tmp_path, json_parsed, json_validation)
    from_json = tasks_repository.load_tasks(tmp_path)[0]

    tasks_repository.save_tasks(tmp_path, [])  # reset store

    yaml_parsed = ti.parse_task_package(yaml.safe_dump(tasks), filename="p.yaml")
    yaml_validation = ti.validate_task_package(yaml_parsed)
    ti.apply_task_package(tmp_path, yaml_parsed, yaml_validation)
    from_yaml = tasks_repository.load_tasks(tmp_path)[0]

    for field in ("id", "project", "title", "goal", "prompt", "status", "priority", "task_type", "branch"):
        assert from_json[field] == from_yaml[field]
    assert from_json["metadata"]["source"] == from_yaml["metadata"]["source"] == "Founder Audit"
    assert from_json["metadata"]["parallel_group"] == from_yaml["metadata"]["parallel_group"] == "WAVE-1"


def test_markdown_front_matter_package_applies_end_to_end(tmp_path):
    yaml = pytest.importorskip("yaml")
    body = yaml.safe_dump([_task(id="PKG-MD")])
    text = f"---\n{body}---\n\n# Report\n"
    parsed = ti.parse_task_package(text, filename="report.md")
    validation = ti.validate_task_package(parsed)
    result = ti.apply_task_package(tmp_path, parsed, validation)
    assert result.imported_ids == ["PKG-MD"]
    assert [t["id"] for t in tasks_repository.load_tasks(tmp_path)] == ["PKG-MD"]


# --------------------------------------------------------------------------
# Atomic persistence / rollback
# --------------------------------------------------------------------------


def test_apply_rolls_back_and_leaves_store_unchanged_when_the_save_fails(tmp_path, monkeypatch):
    """A failure during the single `save_tasks` call must leave `data/tasks.json`
    exactly as it was before the import — atomic persistence + rollback: no
    partial batch, no lost pre-existing task. `save_tasks`'s tempfile +
    `os.replace` guarantees the on-disk file is either the old content or the
    new content, never a half-written mix; here the replace never happens, so
    the pre-existing task survives intact and the new package's tasks are
    absent."""
    prior = ti.parse_task_package(json.dumps([_task(id="PKG-KEEP")]), filename="a.json")
    ti.apply_task_package(tmp_path, prior, ti.validate_task_package(prior))
    assert [t["id"] for t in tasks_repository.load_tasks(tmp_path)] == ["PKG-KEEP"]

    def boom(root, tasks):
        raise RuntimeError("disk full")

    monkeypatch.setattr(tasks_repository, "save_tasks", boom)

    new_pkg = ti.parse_task_package(json.dumps([_task(id="PKG-NEW")]), filename="b.json")
    with pytest.raises(RuntimeError):
        ti.apply_task_package(tmp_path, new_pkg, ti.validate_task_package(new_pkg))

    monkeypatch.undo()
    stored_ids = [t["id"] for t in tasks_repository.load_tasks(tmp_path)]
    assert stored_ids == ["PKG-KEEP"]


# --------------------------------------------------------------------------
# Oversized-input guard
# --------------------------------------------------------------------------


def test_parse_rejects_input_larger_than_max_bytes():
    """An oversized blob is refused before any parser touches it — a clean
    `TaskImportError`, not an unbounded parse."""
    oversized = json.dumps([_task(id="PKG-BIG")])
    with pytest.raises(ti.TaskImportError, match="слишком большой"):
        ti.parse_task_package(oversized, filename="big.json", max_bytes=10)


def test_parse_accepts_input_at_the_size_limit():
    text = json.dumps([_task(id="PKG-FIT")])
    parsed = ti.parse_task_package(text, filename="fit.json", max_bytes=len(text.encode("utf-8")))
    assert [t["id"] for t in parsed.tasks] == ["PKG-FIT"]


def test_default_max_bytes_admits_a_realistic_package():
    # A few hundred tasks is well under the 5 MiB default ceiling.
    text = json.dumps([_task(id=f"PKG-{i:04d}") for i in range(300)])
    assert len(text.encode("utf-8")) < ti.MAX_PACKAGE_BYTES
    parsed = ti.parse_task_package(text, filename="many.json")
    assert len(parsed.tasks) == 300
