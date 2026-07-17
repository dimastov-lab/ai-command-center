"""Unit tests for `command_center.artifacts` — plain-pytest, Streamlit-free.

Pins the exact behavior of `list_markdown_files`, `project_from_path`, and
`infer_task_type_from_filename` as extracted verbatim from `app.py`.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from command_center import artifacts


def test_importing_artifacts_does_not_import_streamlit():
    """Spawns a fresh interpreter (rather than checking `sys.modules` in-process,
    which would be order-dependent on whatever other test modules already
    imported Streamlit this session) to verify `command_center.artifacts`'s own
    import graph never pulls in Streamlit."""
    result = subprocess.run(
        [sys.executable, "-c", "import command_center.artifacts, sys; assert 'streamlit' not in sys.modules"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_list_markdown_files_empty_for_missing_directory(tmp_path):
    assert artifacts.list_markdown_files(tmp_path / "does-not-exist") == []


def test_list_markdown_files_empty_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert artifacts.list_markdown_files(empty) == []


def test_list_markdown_files_ignores_non_markdown_files(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.py").write_text("c")
    files = artifacts.list_markdown_files(tmp_path)
    assert [f.name for f in files] == ["a.md"]


def test_list_markdown_files_recursive_nested_project_paths(tmp_path):
    (tmp_path / "AIOS").mkdir()
    (tmp_path / "AIOS" / "task1.md").write_text("1")
    (tmp_path / "BANK" / "sub").mkdir(parents=True)
    (tmp_path / "BANK" / "sub" / "task2.md").write_text("2")

    files = artifacts.list_markdown_files(tmp_path)
    names = {f.name for f in files}
    assert names == {"task1.md", "task2.md"}


def test_list_markdown_files_sorted_mtime_descending(tmp_path):
    older = tmp_path / "older.md"
    older.write_text("old")
    time.sleep(0.01)
    newer = tmp_path / "newer.md"
    newer.write_text("new")

    files = artifacts.list_markdown_files(tmp_path)
    assert files == [newer, older]


def test_project_from_path_file_under_project_subdirectory(tmp_path):
    base = tmp_path / "generated"
    path = base / "AIOS" / "task_implementation.md"
    assert artifacts.project_from_path(path, base) == "AIOS"


def test_project_from_path_file_directly_under_base(tmp_path):
    base = tmp_path / "generated"
    path = base / "task_implementation.md"
    assert artifacts.project_from_path(path, base) == "—"


def test_project_from_path_not_under_base(tmp_path):
    base = tmp_path / "generated"
    other = tmp_path / "elsewhere" / "task.md"
    assert artifacts.project_from_path(other, base) == "—"


def test_infer_task_type_from_filename_valid_stem():
    assert artifacts.infer_task_type_from_filename(Path("abc123_implementation.md")) == "implementation"
    assert artifacts.infer_task_type_from_filename(Path("abc123_review.md")) == "review"


def test_infer_task_type_from_filename_unrecognized_task_type():
    assert artifacts.infer_task_type_from_filename(Path("abc123_not_a_type.md")) is None


def test_infer_task_type_from_filename_no_underscore_returns_none():
    assert artifacts.infer_task_type_from_filename(Path("noundescore.md")) is None


def test_read_text_returns_file_contents(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# Report\ncontent", encoding="utf-8")
    assert artifacts.read_text(path) == "# Report\ncontent"


def test_read_text_missing_file_returns_empty_string(tmp_path):
    assert artifacts.read_text(tmp_path / "missing.md") == ""
