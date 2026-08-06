"""Tests for get_repository() factory and JSONTasksRepository (Task 3 of AICC Sprint 4)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


def test_default_backend_is_json(tmp_path):
    """Without env var, get_repository returns the JSON-backed port."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AICC_TASKS_BACKEND", None)
        from command_center.tasks_repository import get_repository
        repo = get_repository(tmp_path)
        assert repo.__class__.__name__ == "JSONTasksRepository"


def test_explicit_json_backend(tmp_path):
    """AICC_TASKS_BACKEND=json always returns JSON backend."""
    with patch.dict(os.environ, {"AICC_TASKS_BACKEND": "json"}):
        from importlib import reload
        import command_center.tasks_repository as tr_module
        reload(tr_module)
        repo = tr_module.get_repository(tmp_path)
        assert repo.__class__.__name__ == "JSONTasksRepository"


def test_aios_backend_raises_without_url(tmp_path):
    """AICC_TASKS_BACKEND=aios without AICC_AIOS_URL → RuntimeError."""
    env = {"AICC_TASKS_BACKEND": "aios"}
    env.pop("AICC_AIOS_URL", None)
    env.pop("AICC_AIOS_TOKEN", None)
    with patch.dict(os.environ, env):
        os.environ.pop("AICC_AIOS_URL", None)
        os.environ.pop("AICC_AIOS_TOKEN", None)
        from importlib import reload
        import command_center.tasks_repository as tr_module
        reload(tr_module)
        with pytest.raises(RuntimeError, match="AICC_AIOS_URL"):
            tr_module.get_repository(tmp_path)


def test_aios_backend_raises_without_token(tmp_path):
    """AICC_TASKS_BACKEND=aios с URL, но без AICC_AIOS_TOKEN → RuntimeError."""
    with patch.dict(os.environ, {"AICC_TASKS_BACKEND": "aios", "AICC_AIOS_URL": "http://localhost:5000"}):
        os.environ.pop("AICC_AIOS_TOKEN", None)
        from importlib import reload
        import command_center.tasks_repository as tr_module
        reload(tr_module)
        with pytest.raises(RuntimeError, match="AICC_AIOS_TOKEN"):
            tr_module.get_repository(tmp_path)


def test_json_backend_load_tasks_is_empty_on_fresh_dir(tmp_path):
    """JSONTasksRepository.load_all() returns [] for a fresh directory."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AICC_TASKS_BACKEND", None)
        from command_center.tasks_repository import get_repository
        repo = get_repository(tmp_path)
        assert repo.load_all() == []


def test_json_backend_create_and_load(tmp_path):
    """Create a task via JSONTasksRepository, then load it back."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AICC_TASKS_BACKEND", None)
        from command_center.tasks_repository import get_repository, new_task_record
        repo = get_repository(tmp_path)
        record = new_task_record("AICC", "Test task", "task", "Backlog")
        repo.create(record)
        tasks = repo.load_all()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Test task"
