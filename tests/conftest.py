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

    reports_dir = isolated_data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_runner, "REPORTS_ROOT", reports_dir)
