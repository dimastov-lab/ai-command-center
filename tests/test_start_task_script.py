from __future__ import annotations

import os
import subprocess
from pathlib import Path

from command_center import models


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "start-task.sh"


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _prepare_root(tmp_path: Path) -> Path:
    fake_root = tmp_path / "repo"
    _write(fake_root / "CURRENT_STATE.md")
    _write(fake_root / "context" / "AIOS_CONTEXT.md")
    _write(fake_root / "context" / "BANK_CONTEXT.md")
    _write(fake_root / "context" / "LEGAL_CONTEXT.md")

    for project_id in models.PROJECT_IDS:
        if project_id == "BANK":
            _write(fake_root / "projects" / "BANK_STRATEGY.md")
        else:
            _write(fake_root / "projects" / f"{project_id}.md")
    return fake_root


def test_start_task_supports_every_canonical_project_id(tmp_path):
    fake_root = _prepare_root(tmp_path)

    for project_id in models.PROJECT_IDS:
        env = dict(os.environ)
        env["AICC_START_TASK_ROOT"] = str(fake_root)
        completed = subprocess.Popen(
            [str(SCRIPT), project_id, "implementation", f"Objective for {project_id}"],
            env=env,
        ).wait()
        assert completed == 0, project_id

        generated_dir = fake_root / "generated" / project_id
        assert any(generated_dir.glob("*_implementation.md"))
        assert (fake_root / "reports" / project_id).is_dir()
