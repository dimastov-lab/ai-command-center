"""Clean-venv proof that the pinned artifact satisfies both AICC gateways."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import venv


def test_pinned_artifact_imports_and_builds_aicc_gateways_in_clean_venv(tmp_path):
    raw_wheel = os.environ.get("AICC_AIOS_SDK_WHEEL", "")
    assert raw_wheel, "AICC_AIOS_SDK_WHEEL must identify the verified pinned artifact"
    wheel = Path(raw_wheel).resolve()
    assert wheel.is_file(), f"verified SDK wheel is missing: {wheel}"

    environment = tmp_path / "clean-sdk-env"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        timeout=120,
    )
    source_root = Path(__file__).resolve().parents[1]
    probe = """
from pathlib import Path
from command_center.application import aios_tasks
sdk = aios_tasks._load_aios_sdk()
aios_tasks.validate_aios_sdk_contract(sdk)
tasks = aios_tasks.build_aios_tasks_repository(
    url='https://example.test', token='secret', map_path=Path('unused-map.json')
)
status = aios_tasks.build_aios_status_client(
    url='https://example.test', token='secret', tenant_id='tenant-1', workspace_id='ws-1'
)
tasks.close()
status.close()
assert sdk.__version__ == '0.2.0'
"""
    clean_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source_root)}
    completed = subprocess.run(
        [str(python), "-c", probe],
        cwd=tmp_path,
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
