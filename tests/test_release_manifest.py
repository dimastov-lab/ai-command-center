from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from command_center.release_manifest import build_release_manifest

SOURCE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_build_release_manifest_emits_hashes_signing_and_relative_paths(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    web = dist / "web.tar.gz"
    wheel = dist / "aios_sdk-0.2.0-py3-none-any.whl"
    web_bytes = b"web-build"
    wheel_bytes = b"wheel-build"
    web.write_bytes(web_bytes)
    wheel.write_bytes(wheel_bytes)

    manifest = build_release_manifest(
        source_sha=SOURCE_SHA,
        artifact_specs=[
            {
                "path": web.as_posix(),
                "platform": "web",
                "artifact_type": "web_bundle",
                "signing_required": False,
                "signing_status": "not_applicable",
            },
            {
                "path": wheel.as_posix(),
                "platform": "cross-platform",
                "artifact_type": "python_wheel",
                "signing_required": True,
                "signing_status": "unknown",
                "signing_identity": None,
            },
        ],
        repository_root=tmp_path,
    )

    assert manifest["schema_version"] == 1
    assert manifest["source_sha"] == SOURCE_SHA
    assert manifest["artifacts"] == [
        {
            "artifact_type": "python_wheel",
            "file_name": "aios_sdk-0.2.0-py3-none-any.whl",
            "platform": "cross-platform",
            "relative_path": "dist/aios_sdk-0.2.0-py3-none-any.whl",
            "sha256": _sha256_bytes(wheel_bytes),
            "signing": {"required": True, "status": "unknown", "identity": None},
            "size_bytes": len(wheel_bytes),
        },
        {
            "artifact_type": "web_bundle",
            "file_name": "web.tar.gz",
            "platform": "web",
            "relative_path": "dist/web.tar.gz",
            "sha256": _sha256_bytes(web_bytes),
            "signing": {"required": False, "status": "not_applicable", "identity": None},
            "size_bytes": len(web_bytes),
        },
    ]


def test_build_release_manifest_rejects_invalid_source_sha(tmp_path):
    file_path = tmp_path / "artifact.bin"
    file_path.write_bytes(b"x")
    with pytest.raises(ValueError, match="source_sha"):
        build_release_manifest(
            source_sha="invalid",
            artifact_specs=[
                {
                    "path": file_path.as_posix(),
                    "platform": "linux",
                    "artifact_type": "desktop_bundle",
                    "signing_status": "unknown",
                }
            ],
            repository_root=tmp_path,
        )


def test_build_release_manifest_rejects_invalid_signing_status(tmp_path):
    file_path = tmp_path / "artifact.bin"
    file_path.write_bytes(b"x")
    with pytest.raises(ValueError, match="signing_status"):
        build_release_manifest(
            source_sha=SOURCE_SHA,
            artifact_specs=[
                {
                    "path": file_path.as_posix(),
                    "platform": "linux",
                    "artifact_type": "desktop_bundle",
                    "signing_status": "approved",
                }
            ],
            repository_root=tmp_path,
        )


def test_build_release_manifest_script_writes_json(tmp_path):
    artifact = tmp_path / "installer.pkg"
    artifact.write_bytes(b"installer")
    spec_file = tmp_path / "spec.json"
    output = tmp_path / "release-manifest.json"
    spec_file.write_text(
        json.dumps(
            [
                {
                    "path": artifact.as_posix(),
                    "platform": "macos",
                    "artifact_type": "desktop_installer",
                    "signing_required": True,
                    "signing_status": "unknown",
                }
            ]
        ),
        encoding="utf-8",
    )

    script = Path(__file__).resolve().parents[1] / "scripts" / "build_release_manifest.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-sha",
            SOURCE_SHA,
            "--spec-file",
            str(spec_file),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_sha"] == SOURCE_SHA
    assert payload["artifacts"][0]["platform"] == "macos"
