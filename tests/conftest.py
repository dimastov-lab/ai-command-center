"""Isolates every test in this session from the developer's real `data/` directory.

`AICC_DATA_DIR` (see `command_center.storage.resolve_data_dir`) is set here, at
conftest import time — before pytest imports any test module or `app.py` — so every
`command_center` module's file-level `DATA_DIR`/`*_FILE` constants resolve into a
throwaway temp directory instead of the developer's real `data/tasks.json`,
`data/runs.jsonl`, etc. Those constants are computed once, at first import, so all
tests in a session share the same temp directory; `isolated_data_dir` (autouse) resets
its *contents* between tests rather than re-pointing it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aicc_test_data_"))
os.environ["AICC_DATA_DIR"] = str(_TEST_DATA_DIR)


@pytest.fixture(autouse=True)
def isolated_data_dir():
    if _TEST_DATA_DIR.exists():
        shutil.rmtree(_TEST_DATA_DIR)
    _TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield _TEST_DATA_DIR
    if _TEST_DATA_DIR.exists():
        shutil.rmtree(_TEST_DATA_DIR)


@pytest.fixture(autouse=True)
def isolated_reports_dir(isolated_data_dir, monkeypatch):
    """`agent_runner.REPORTS_ROOT` is a module-level constant derived from `ROOT`, not
    `AICC_DATA_DIR` (reports live at `<repo>/reports/`, not under `data/`). Any test
    that exercises a full launch flow — including via Streamlit `AppTest`, which runs
    the real `app.py` — would otherwise write real files into the developer's actual
    `reports/<PROJECT>/` directory. Applied to every test automatically so nobody has
    to remember it (a real leak into `reports/AIOS/` from this exact gap was caught
    and cleaned up manually before this fixture existed)."""
    from command_center import agent_runner
    from command_center.runtime import reports as runtime_reports

    reports_dir = isolated_data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_runner, "REPORTS_ROOT", reports_dir)
    monkeypatch.setattr(runtime_reports, "REPORTS_ROOT", reports_dir)


# --------------------------------------------------------------------------
# v2 runtime (Session Supervisor) test fixtures
# --------------------------------------------------------------------------

FAKE_CLAUDE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_claude.py"


@pytest.fixture
def git_repo(tmp_path):
    """A real, throwaway git repository — never one of the user's real projects."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def configure_project_repo(monkeypatch):
    """Returns `configure(project_id, repo_path)`, patching project_config so
    that project resolves to `repo_path` for both `agent_runner.validate_repository`
    (used by `supervisor.Supervisor.start_raw`) and any direct caller."""
    from command_center import project_config

    def configure(project_id: str, repo_path: Path) -> None:
        def fake_get_project_config(pid, _repo_path=str(repo_path), _project_id=project_id):
            cfg = project_config.default_project_config(pid)
            if pid == _project_id:
                cfg["repository_path"] = _repo_path
            return cfg

        monkeypatch.setattr(project_config, "get_project_config", fake_get_project_config)
        from command_center import agent_runner

        monkeypatch.setattr(agent_runner.project_config, "get_project_config", fake_get_project_config)

    return configure


@pytest.fixture
def fake_claude(monkeypatch):
    """Points `command_center.runtime.supervisor` at `tests/fixtures/fake_claude.py`
    (run under the *same* Python interpreter as the test) instead of the real
    `claude` binary, so supervisor tests exercise a genuine `subprocess.Popen`
    (real pid, real process group, real signal delivery) without ever invoking
    the real CLI or spending API credits. Returns a dict of env-var overrides
    the test can mutate before launching a run (see `fixtures/fake_claude.py`
    for the supported keys)."""
    from command_center.runtime import supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module, "CLAUDE_BINARY", sys.executable)

    original_build = supervisor_module.build_claude_command

    def patched_build(**kwargs):
        command = original_build(**kwargs)
        return [command[0], str(FAKE_CLAUDE_SCRIPT)] + command[1:]

    monkeypatch.setattr(supervisor_module, "build_claude_command", patched_build)

    env_overrides: dict[str, str] = {}
    original_popen = subprocess.Popen

    def popen_with_env(*args, **kwargs):
        env = dict(os.environ)
        env.update(env_overrides)
        kwargs["env"] = env
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", popen_with_env)

    return env_overrides


FAKE_CLAUDE_TREE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_claude_tree.py"


@pytest.fixture
def fake_claude_tree(monkeypatch, tmp_path):
    """Like `fake_claude`, but points at `fixtures/fake_claude_tree.py`, which
    spawns a real parent -> child -> grandchild process tree in one process
    group (F4: grandchild cancellation regression coverage). Returns
    `(env_overrides, pidfile_base)` — `pidfile_base + ".parent"/".child"/
    ".grandchild"` are written once the whole tree is up."""
    from command_center.runtime import supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module, "CLAUDE_BINARY", sys.executable)

    original_build = supervisor_module.build_claude_command

    def patched_build(**kwargs):
        command = original_build(**kwargs)
        return [command[0], str(FAKE_CLAUDE_TREE_SCRIPT)] + command[1:]

    monkeypatch.setattr(supervisor_module, "build_claude_command", patched_build)

    pidfile_base = str(tmp_path / "tree_pids")
    env_overrides: dict[str, str] = {"FAKE_CLAUDE_TREE_PIDFILE": pidfile_base}
    original_popen = subprocess.Popen

    def popen_with_env(*args, **kwargs):
        env = dict(os.environ)
        env.update(env_overrides)
        kwargs["env"] = env
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", popen_with_env)

    return env_overrides, pidfile_base
