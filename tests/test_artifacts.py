"""Unit tests for `command_center/artifacts.py` — extracted verbatim from `app.py`
(§9/§9.1/§9.2 of WORKSPACE_HOME_ARCHITECTURE.md), pinning today's already-shipped
behavior exactly. No Streamlit import anywhere in this test module or the module
under test.
"""

from __future__ import annotations

import inspect

from command_center import artifacts


def test_module_source_has_no_streamlit_or_app_import():
    """Static check backing §9.2's dependency-direction rule: `artifacts.py` must
    never import Streamlit or `app.py`, directly or transitively."""
    import_lines = [
        line.strip()
        for line in inspect.getsource(artifacts).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("streamlit" in line.lower() for line in import_lines)
    assert not any(line == "import app" or line.startswith("from app ") for line in import_lines)


def test_read_text_missing_file(tmp_path):
    assert artifacts.read_text(tmp_path / "missing.md") == "Файл пока не создан."


def test_read_text_empty_file(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("   \n")
    assert artifacts.read_text(path) == "Файл пока пуст."


def test_read_text_returns_content(tmp_path):
    path = tmp_path / "content.md"
    path.write_text("hello world")
    assert artifacts.read_text(path) == "hello world"


def test_list_markdown_files_missing_directory_returns_empty(tmp_path):
    assert artifacts.list_markdown_files(tmp_path / "does-not-exist") == []


def test_list_markdown_files_empty_directory_returns_empty(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    assert artifacts.list_markdown_files(tmp_path) == []


def test_list_markdown_files_ignores_non_markdown(tmp_path):
    (tmp_path / "note.txt").write_text("x")
    assert artifacts.list_markdown_files(tmp_path) == []


def test_list_markdown_files_sorted_mtime_descending(tmp_path):
    import os
    import time

    old = tmp_path / "old.md"
    old.write_text("old")
    time.sleep(0.02)
    new = tmp_path / "new.md"
    new.write_text("new")

    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    files = artifacts.list_markdown_files(tmp_path)
    assert files == [new, old]


def test_list_markdown_files_recurses_into_project_subdirectories(tmp_path):
    (tmp_path / "AIOS").mkdir()
    nested = tmp_path / "AIOS" / "task_implementation.md"
    nested.write_text("x")
    files = artifacts.list_markdown_files(tmp_path)
    assert files == [nested]


def test_project_from_path_one_level_under_base(tmp_path):
    base = tmp_path / "generated"
    path = base / "AIOS" / "task.md"
    assert artifacts.project_from_path(path, base) == "AIOS"


def test_project_from_path_directly_under_base_has_no_project(tmp_path):
    base = tmp_path / "generated"
    path = base / "task.md"
    assert artifacts.project_from_path(path, base) == "—"


def test_project_from_path_not_under_base_returns_dash(tmp_path):
    base = tmp_path / "generated"
    other = tmp_path / "elsewhere" / "task.md"
    assert artifacts.project_from_path(other, base) == "—"


def test_infer_task_type_from_filename_valid_stem(tmp_path):
    path = tmp_path / "abc123_implementation.md"
    assert artifacts.infer_task_type_from_filename(path) == "implementation"


def test_infer_task_type_from_filename_unrecognized_type(tmp_path):
    path = tmp_path / "abc123_not_a_task_type.md"
    assert artifacts.infer_task_type_from_filename(path) is None


def test_infer_task_type_from_filename_no_underscore(tmp_path):
    path = tmp_path / "abc123.md"
    assert artifacts.infer_task_type_from_filename(path) is None


def test_infer_task_type_from_filename_every_recognized_type(tmp_path):
    for task_type in ["implementation", "review", "remediation", "final_gate", "architecture_review"]:
        path = tmp_path / f"id_{task_type}.md"
        assert artifacts.infer_task_type_from_filename(path) == task_type
