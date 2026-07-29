"""Regression for AR-5: config writers must hold a cross-process file lock
across the *whole* read-modify-write, or two concurrent writers lost-update each
other. portfolio_config/project_config previously did an unlocked
`_read_overrides() -> mutate -> atomic_write_json`, which each write is crash-
atomic but two writers can still each read the same pre-write state and clobber.
"""

from __future__ import annotations

from command_center import portfolio_config, project_config, storage


def _spy_file_lock(monkeypatch):
    calls: list = []
    real = storage.file_lock

    def spy(lock_path, **kwargs):
        calls.append(lock_path)
        return real(lock_path, **kwargs)

    monkeypatch.setattr(storage, "file_lock", spy)
    return calls


def test_portfolio_config_save_holds_a_file_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_config, "CONFIG_FILE", tmp_path / "portfolio_config.json")
    monkeypatch.setattr(
        portfolio_config, "CONFIG_LOCK_FILE", tmp_path / "portfolio_config.lock", raising=False
    )
    calls = _spy_file_lock(monkeypatch)

    portfolio_config.save_repository_path("AICC", "/repo/aicc")

    assert calls, "save_repository_path must acquire a file_lock across the RMW (AR-5)"
    assert portfolio_config._read_overrides().get("AICC") == "/repo/aicc"


def test_project_config_save_holds_a_file_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(project_config, "CONFIG_FILE", tmp_path / "project_config.json")
    monkeypatch.setattr(
        project_config, "CONFIG_LOCK_FILE", tmp_path / "project_config.lock", raising=False
    )
    calls = _spy_file_lock(monkeypatch)

    project_config.save_repository_path("AICC", "/repo/aicc")

    assert calls, "project_config.save_repository_path must acquire a file_lock across the RMW (AR-5)"
    assert project_config._read_overrides(), "the override must be persisted"
