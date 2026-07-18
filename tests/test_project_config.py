import subprocess
from pathlib import Path

import pytest

from command_center import executors, models, project_config


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


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


# --------------------------------------------------------------------------
# Project Model: engineering-environment defaults and planning fields
# --------------------------------------------------------------------------


def test_default_project_config_has_every_project_model_field():
    cfg = project_config.default_project_config("AIOS")
    for field in (
        "display_name", "description", "repository_path", "default_workspace_path",
        "default_branch", "default_executor", "default_prompt", "status", "priority",
        "progress", "current_sprint", "current_milestone", "owner",
    ):
        assert field in cfg


def test_default_project_config_engineering_fields_are_unset_for_every_project():
    for project_id in models.PROJECT_IDS:
        cfg = project_config.default_project_config(project_id)
        assert cfg["default_branch"] is None
        assert cfg["default_executor"] is None
        assert cfg["default_prompt"] == ""


def test_save_project_settings_roundtrip():
    project_config.save_project_settings(
        "AIOS",
        default_workspace_path="/ws/aios",
        default_branch="develop",
        default_executor="claude_code",
        default_prompt="Investigate the failing build.",
        description="AIOS engineering project",
        status="Active",
        priority="High",
        progress=40,
        current_sprint="Sprint 7",
        current_milestone="P1 launch",
        owner="Dmitry",
    )
    cfg = project_config.get_project_config("AIOS")
    assert cfg["default_workspace_path"] == "/ws/aios"
    assert cfg["default_branch"] == "develop"
    assert cfg["default_executor"] == "claude_code"
    assert cfg["default_prompt"] == "Investigate the failing build."
    assert cfg["description"] == "AIOS engineering project"
    assert cfg["status"] == "Active"
    assert cfg["priority"] == "High"
    assert cfg["progress"] == 40
    assert cfg["current_sprint"] == "Sprint 7"
    assert cfg["current_milestone"] == "P1 launch"
    assert cfg["owner"] == "Dmitry"


def test_save_project_settings_none_clears_override_back_to_default():
    project_config.save_project_settings("AIOS", default_branch="develop")
    assert project_config.get_project_config("AIOS")["default_branch"] == "develop"

    project_config.save_project_settings("AIOS", default_branch=None)
    assert project_config.get_project_config("AIOS")["default_branch"] is None


def test_save_project_settings_rejects_unknown_project():
    with pytest.raises(ValueError):
        project_config.save_project_settings("NOPE", default_branch="main")


def test_save_project_settings_rejects_unknown_field():
    with pytest.raises(ValueError):
        project_config.save_project_settings("AIOS", not_a_real_field="x")


def test_save_project_settings_does_not_leak_into_other_projects():
    project_config.save_project_settings("AIOS", owner="Dmitry", default_branch="develop")
    configs = project_config.load_project_configs()
    assert configs["BANK"]["owner"] == ""
    assert configs["BANK"]["default_branch"] is None


# --------------------------------------------------------------------------
# Legacy projects: a project_config.json written before these fields existed
# must still load, with every new field falling back to its default.
# --------------------------------------------------------------------------


def test_legacy_project_config_file_with_only_repository_path_still_loads_new_fields_as_defaults(tmp_path):
    project_config.storage.atomic_write_json(
        project_config.CONFIG_FILE, {"AIOS": {"repository_path": str(tmp_path)}}
    )
    cfg = project_config.get_project_config("AIOS")
    assert cfg["repository_path"] == str(tmp_path)
    assert cfg["default_workspace_path"] is None
    assert cfg["default_branch"] is None
    assert cfg["default_executor"] is None
    assert cfg["default_prompt"] == ""
    assert cfg["status"] == "Active"
    assert cfg["priority"] == "Medium"
    assert cfg["progress"] == 0


# --------------------------------------------------------------------------
# Task inheritance / workspace inheritance
# --------------------------------------------------------------------------


def test_task_defaults_from_project_prefers_default_workspace_over_repository_path():
    cfg = {
        "repository_path": "/project/repo",
        "default_workspace_path": "/project/default-ws",
        "default_branch": "develop",
        "default_executor": "claude_code",
        "default_prompt": "Review the module.",
    }
    defaults = project_config.task_defaults_from_project(cfg)
    assert defaults == {
        "workspace_path": "/project/default-ws",
        "branch": "develop",
        "executor": "claude_code",
        "prompt": "Review the module.",
    }


def test_task_defaults_from_project_falls_back_to_repository_path_when_no_default_workspace():
    """Backward compatibility: a project with only `repository_path` set (the
    pre-existing config shape) must still hand a new task a usable
    `workspace_path` — mirroring `launch.resolve_workspace_path`'s own
    fallback — instead of leaving it unset."""
    cfg = {"repository_path": "/project/repo", "default_workspace_path": None}
    defaults = project_config.task_defaults_from_project(cfg)
    assert defaults["workspace_path"] == "/project/repo"
    assert defaults["branch"] is None
    assert defaults["executor"] is None
    assert defaults["prompt"] == ""


def test_task_defaults_from_project_all_unset_when_project_has_nothing_configured():
    cfg = project_config.default_project_config("PERSONAL")
    defaults = project_config.task_defaults_from_project(cfg)
    assert defaults == {"workspace_path": None, "branch": None, "executor": None, "prompt": ""}


# --------------------------------------------------------------------------
# Project validation
# --------------------------------------------------------------------------


def test_validate_project_settings_no_warnings_for_a_valid_config(tmp_path):
    _init_repo(tmp_path)
    cfg = {
        "repository_path": str(tmp_path),
        "default_workspace_path": str(tmp_path),
        "default_branch": None,
        "default_executor": "claude_code",
        "default_prompt": "short prompt",
    }
    assert project_config.validate_project_settings(cfg) == []


def test_validate_project_settings_warns_on_missing_repository_path(tmp_path):
    warnings = project_config.validate_project_settings({"repository_path": str(tmp_path / "does-not-exist")})
    assert any("репозитори" in w.lower() for w in warnings)


def test_validate_project_settings_warns_when_workspace_is_not_a_git_repo(tmp_path):
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    warnings = project_config.validate_project_settings({"default_workspace_path": str(non_repo)})
    assert any("git-репозиторием" in w for w in warnings)


def test_validate_project_settings_warns_on_unknown_branch(tmp_path):
    _init_repo(tmp_path)
    warnings = project_config.validate_project_settings(
        {"default_workspace_path": str(tmp_path), "default_branch": "does-not-exist-branch"}
    )
    assert any("не найдена" in w for w in warnings)


def test_validate_project_settings_warns_on_unknown_executor():
    warnings = project_config.validate_project_settings({"default_executor": "not_a_real_executor"})
    assert any("Неизвестный executor" in w for w in warnings)


def test_validate_project_settings_warns_on_unavailable_but_known_executor():
    unavailable = next(e for e in executors.EXECUTORS.values() if not e.available)
    warnings = project_config.validate_project_settings({"default_executor": unavailable.id})
    assert any("недоступен" in w for w in warnings)


def test_validate_project_settings_never_raises_and_never_blocks(tmp_path):
    """Warnings are advisory only — `validate_project_settings` must never
    raise, even for a thoroughly broken candidate config, so the settings UI
    can always still save (matching the pre-existing repository-path field's
    own tolerance)."""
    warnings = project_config.validate_project_settings(
        {
            "repository_path": "relative/not/absolute",
            "default_workspace_path": str(tmp_path / "missing"),
            "default_branch": "whatever",
            "default_executor": "bogus",
        }
    )
    assert isinstance(warnings, list)
    assert warnings  # multiple problems, all surfaced as warnings, not exceptions
