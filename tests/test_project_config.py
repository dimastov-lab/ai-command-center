from pathlib import Path

import pytest

from command_center import models, project_config


def test_default_config_has_no_repository_path_for_any_project():
    for project_id in models.PROJECT_IDS:
        cfg = project_config.default_project_config(project_id)
        assert cfg["repository_path"] is None


def test_sensitive_projects_flagged():
    assert project_config.is_sensitive("BANK") is True
    assert project_config.is_sensitive("LEGAL") is True
    assert project_config.is_sensitive("AIOS") is False


def test_discover_candidate_returns_none_for_unmapped_project():
    assert project_config.discover_candidate_repository_path("PERSONAL") is None


def test_discover_candidate_never_guesses_a_non_git_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert project_config.discover_candidate_repository_path("AIOS") is None

    candidate_dir = tmp_path / "Projects" / "aios"
    candidate_dir.mkdir(parents=True)
    # Directory exists but has no .git — must still refuse to suggest it.
    assert project_config.discover_candidate_repository_path("AIOS") is None


def test_discover_candidate_returns_verified_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    candidate_dir = tmp_path / "Projects" / "aios"
    (candidate_dir / ".git").mkdir(parents=True)
    assert project_config.discover_candidate_repository_path("AIOS") == str(candidate_dir)


def test_validate_repository_path_rejects_empty():
    ok, _ = project_config.validate_repository_path("")
    assert ok is False


def test_validate_repository_path_rejects_relative():
    ok, _ = project_config.validate_repository_path("relative/path")
    assert ok is False


def test_validate_repository_path_rejects_missing(tmp_path):
    ok, _ = project_config.validate_repository_path(str(tmp_path / "does-not-exist"))
    assert ok is False


def test_validate_repository_path_rejects_a_file(tmp_path):
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x")
    ok, _ = project_config.validate_repository_path(str(file_path))
    assert ok is False


def test_validate_repository_path_accepts_existing_dir(tmp_path):
    ok, message = project_config.validate_repository_path(str(tmp_path))
    assert ok is True
    assert message == "OK"


def test_save_and_load_repository_path_roundtrip(tmp_path):
    project_config.save_repository_path("AIOS", str(tmp_path))
    assert project_config.get_project_config("AIOS")["repository_path"] == str(tmp_path)

    project_config.save_repository_path("AIOS", None)
    assert project_config.get_project_config("AIOS")["repository_path"] is None


def test_save_repository_path_rejects_unknown_project():
    with pytest.raises(ValueError):
        project_config.save_repository_path("NOPE", "/tmp")


def test_saved_path_does_not_leak_into_other_projects(tmp_path):
    project_config.save_repository_path("AIOS", str(tmp_path))
    configs = project_config.load_project_configs()
    assert configs["BANK"]["repository_path"] is None
    assert configs["LEGAL"]["repository_path"] is None


def test_default_config_has_no_default_workspace_path_for_any_project():
    for project_id in models.PROJECT_IDS:
        cfg = project_config.default_project_config(project_id)
        assert cfg["default_workspace_path"] is None


def test_default_workspace_path_override_is_loaded_from_config_file(tmp_path):
    project_config.storage.atomic_write_json(
        project_config.CONFIG_FILE,
        {"AIOS": {"repository_path": str(tmp_path / "repo"), "default_workspace_path": str(tmp_path / "default")}},
    )
    cfg = project_config.get_project_config("AIOS")
    assert cfg["default_workspace_path"] == str(tmp_path / "default")
    assert cfg["repository_path"] == str(tmp_path / "repo")
