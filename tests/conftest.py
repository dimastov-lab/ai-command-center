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
    _clear_execution_center_singleton_cache()
    yield _TEST_DATA_DIR
    if _TEST_DATA_DIR.exists():
        shutil.rmtree(_TEST_DATA_DIR)


def _clear_execution_center_singleton_cache() -> None:
    """`app.py`'s `get_execution_center_api()` is `@st.cache_resource` —
    cached process-wide, not per-`AppTest`-instance. Since the Live
    Execution Center v2 bridge means *any* Kanban task launch (not just a
    visit to the Live Execution Center page) now constructs that singleton,
    every test that resets `AICC_DATA_DIR` must also clear this cache —
    otherwise a `Supervisor` cached from a previous test would keep pointing
    at a `runtime.db` path this fixture just deleted and recreated fresh
    (unmigrated), surfacing as `sqlite3.OperationalError: no such table:
    run`. Streamlit is imported lazily here so modules that never touch
    Streamlit at all aren't forced to import it just for this cleanup."""
    import streamlit as st

    st.cache_resource.clear()


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


@pytest.fixture(autouse=True)
def isolated_module_data_constants(isolated_data_dir, monkeypatch):
    """Defense-in-depth for the same class of gap `isolated_reports_dir` closes for
    `REPORTS_ROOT`, applied to every module that computes `DATA_DIR`/`*_FILE` once at
    first import (`project_config.py`, `agent_runner.py`, `activity_log.py`,
    `chat_service.py` — see each module's `DATA_DIR = storage.resolve_data_dir(ROOT)`
    line). Setting `AICC_DATA_DIR` before pytest imports any test module (see the
    module docstring above) is sufficient for a normal `pytest` invocation, but it is
    not the only way these modules can end up imported: a persistent Python process
    (a REPL, a notebook, an agent running a one-off repro snippet) that imports one of
    them *before* `AICC_DATA_DIR` is set freezes `DATA_DIR` to the real `data/`
    directory for the rest of that process, and a subsequent
    `save_repository_path`/`log_event`/etc. call silently writes real files instead of
    raising — this is exactly how a pytest `tmp_path` (e.g.
    `.../pytest-184/test_full_launch_flow_records_0/aios-fake-repo`) ended up
    persisted into the developer's real `data/project_config.json` `AIOS.repository_path`
    (found and fixed as part of the runtime-integrity incident this fixture responds
    to). Directly monkeypatching each module's constants — rather than relying solely
    on import ordering — makes isolation hold regardless of when/how each module was
    first imported into the test process."""
    from command_center import activity_log, agent_runner, chat_service, project_config

    monkeypatch.setattr(project_config, "DATA_DIR", isolated_data_dir)
    monkeypatch.setattr(project_config, "CONFIG_FILE", isolated_data_dir / "project_config.json")
    monkeypatch.setattr(agent_runner, "DATA_DIR", isolated_data_dir)
    monkeypatch.setattr(agent_runner, "RUNS_FILE", isolated_data_dir / "runs.jsonl")
    monkeypatch.setattr(activity_log, "DATA_DIR", isolated_data_dir)
    monkeypatch.setattr(activity_log, "ACTIVITY_FILE", isolated_data_dir / "activity.jsonl")
    monkeypatch.setattr(chat_service, "DATA_DIR", isolated_data_dir)
    monkeypatch.setattr(chat_service, "CHATS_FILE", isolated_data_dir / "chats.json")


_CONTAMINATION_MARKERS: tuple[str, ...] = (
    "pytest-of-",
    "aicc_test_data_",
    "/pytest-",  # e.g. .../T/pytest-184/...
    "-fake-repo",
)


@pytest.fixture(scope="session", autouse=True)
def guard_real_project_files():
    """Session-wide regression guard for the exact failure this fixture module
    responds to: a pytest-generated temp path (`tmp_path`, `pytest-of-<user>/
    pytest-<N>/...`) or an isolated test data dir path ending up persisted inside the
    developer's real `data/*.json(l)`/`data/project_config.json` or `reports/`
    content. Scans both trees for `_CONTAMINATION_MARKERS` once at session end and
    fails loudly, naming the exact file and matched marker, if any are found.

    Deliberately **not** a byte-for-byte before/after diff: this repo is a
    self-hosted dev tool the developer routinely runs live (`streamlit run app.py`)
    while also running the test suite, and that live process legitimately rewrites
    `data/tasks.json`/`data/execution_queue.json`/`data/activity.jsonl` on its own
    schedule (Live Execution Center polling, queue re-evaluation) — a strict diff
    guard flags that normal concurrent usage as a false positive (confirmed while
    building this fixture: a live `streamlit run app.py` process was mutating
    `execution_queue.json`'s `evaluated_at` timestamps mid test-run, unrelated to the
    test suite). Marker-scanning targets the actual contamination signature instead
    of "any change," so it stays silent for legitimate concurrent app activity and
    loud only for genuine test-fixture leakage."""
    root = Path(__file__).resolve().parent.parent
    yield

    def _scan(directory: Path) -> list[str]:
        if not directory.is_dir():
            return []
        hits = []
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            haystacks = [str(path)]
            if path.suffix in (".json", ".jsonl", ".md"):
                try:
                    haystacks.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
            for haystack in haystacks:
                for marker in _CONTAMINATION_MARKERS:
                    if marker in haystack:
                        hits.append(f"{path}: matched {marker!r}")
                        break
        return hits

    hits = _scan(root / "data") + _scan(root / "reports")
    assert not hits, "Found test-fixture contamination in real project files:\n" + "\n".join(hits)


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
