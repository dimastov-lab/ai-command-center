"""Clean-venv proof that the pinned artifact satisfies both AICC gateways."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import venv

import pytest


def _resolve_pinned_sdk_wheel(tmp_path: Path) -> Path:
    raw_wheel = os.environ.get("AICC_AIOS_SDK_WHEEL", "")
    if raw_wheel:
        wheel = Path(raw_wheel).resolve()
        if wheel.is_file():
            return wheel

    source_root = Path(__file__).resolve().parents[1]
    lock = json.loads((source_root / "aios-sdk.lock.json").read_text(encoding="utf-8"))
    expected_filename = str(lock["wheel_filename"])
    expected_sha256 = str(lock["wheel_sha256"])
    local_candidate = source_root / ".artifacts" / expected_filename
    if local_candidate.is_file():
        digest = hashlib.sha256(local_candidate.read_bytes()).hexdigest()
        if digest == expected_sha256:
            return local_candidate

    try:
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                str(lock["release_tag"]),
                "--repo",
                str(lock["repository"]),
                "--pattern",
                expected_filename,
                "--dir",
                str(tmp_path / "release-asset"),
            ],
            check=True,
            timeout=120,
        )
        wheel_bytes = (tmp_path / "release-asset" / expected_filename).read_bytes()
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        pytest.skip(
            "Pinned AIOS SDK wheel is unavailable locally. "
            "Set AICC_AIOS_SDK_WHEEL or place the verified wheel in .artifacts/."
        )
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    assert digest == expected_sha256, "downloaded pinned SDK artifact checksum mismatch"
    wheel = tmp_path / expected_filename
    with wheel.open("wb") as handle:
        handle.write(wheel_bytes)
    return wheel


def test_pinned_artifact_imports_and_builds_aicc_gateways_in_clean_venv(tmp_path):
    wheel = _resolve_pinned_sdk_wheel(tmp_path).resolve()

    environment = tmp_path / "clean-sdk-env"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        timeout=120,
    )
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
    source_root = Path(__file__).resolve().parents[1]
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
