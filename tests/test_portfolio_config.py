from command_center import portfolio_config


def test_load_repository_paths_includes_seeded_defaults():
    mapping = portfolio_config.load_repository_paths()
    assert "AICC" in mapping
    assert "PRODUCT" in mapping


def test_unseeded_project_has_no_default_mapping():
    mapping = portfolio_config.load_repository_paths()
    assert "AIOS" not in mapping
    assert "AICOS" not in mapping
    assert "INFRA" not in mapping


def test_save_repository_path_persists_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    portfolio_config.save_repository_path("AIOS", str(repo))
    assert portfolio_config.get_repository_path("AIOS") == str(repo)


def test_save_repository_path_none_clears_override():
    repo_path = "/tmp/does-not-need-to-exist-for-this-test"
    portfolio_config.save_repository_path("AIOS", repo_path)
    assert portfolio_config.get_repository_path("AIOS") == repo_path

    portfolio_config.save_repository_path("AIOS", None)
    assert portfolio_config.get_repository_path("AIOS") is None


def test_save_repository_path_can_clear_a_seeded_default():
    portfolio_config.save_repository_path("AICC", None)
    assert portfolio_config.get_repository_path("AICC") is None


def test_validate_repository_path_reused_from_project_config():
    ok, message = portfolio_config.validate_repository_path("")
    assert ok is False
    assert message
