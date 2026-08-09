from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from scripts.fetch_aios_sdk_artifact import (
    ArtifactError,
    extract_verified_artifact,
    load_lock,
    validate_artifact_metadata,
)


def _archive(filename: str, wheel: bytes, checksum: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, wheel)
        archive.writestr("SHA256SUMS", f"{checksum}  {filename}\n")
    return buffer.getvalue()


def test_sdk_lock_is_exact_and_contains_no_mutable_or_sibling_fallback():
    lock = load_lock()
    assert lock.repository == "dimastov-lab/aios"
    assert lock.source_sha == "06fbaf2ccefaf675ced3959051c14ff78f7a89d8"
    assert lock.accepted_main_sha == "acaa035386a4c9aca4bf901c24c1669745d8405f"
    assert lock.run_id == 31282670546
    assert lock.artifact_id == 9028887683
    assert lock.wheel_sha256 == "48cc8b028d6a0f7f4be56d385c502cd5a5bfe34b26de0416d6bf30ad58942a0e"
    rendered = json.dumps(lock.as_dict())
    assert "../aios" not in rendered
    assert '"main"' not in rendered
    assert '"latest"' not in rendered


def test_artifact_extracts_only_exact_checksum_verified_wheel(tmp_path):
    lock = load_lock()
    wheel = b"wheel-bytes"
    checksum = hashlib.sha256(wheel).hexdigest()
    test_lock = lock.with_wheel_sha256(checksum)
    archive = _archive(lock.wheel_filename, wheel, checksum)
    path = extract_verified_artifact(archive, tmp_path, test_lock)
    assert path.read_bytes() == wheel


def test_checksum_mismatch_fails_closed_without_writing_wheel(tmp_path):
    lock = load_lock()
    archive = _archive(lock.wheel_filename, b"tampered", lock.wheel_sha256)
    with pytest.raises(ArtifactError, match="checksum"):
        extract_verified_artifact(archive, tmp_path, lock)
    assert not (tmp_path / lock.wheel_filename).exists()


def test_artifact_metadata_must_bind_accepted_main_run_and_name():
    lock = load_lock()
    payload = {
        "id": lock.artifact_id,
        "name": lock.artifact_name,
        "expired": False,
        "workflow_run": {"id": lock.run_id, "head_sha": lock.accepted_main_sha},
    }
    validate_artifact_metadata(payload, lock)
    payload["workflow_run"]["head_sha"] = lock.source_sha
    with pytest.raises(ArtifactError, match="identity"):
        validate_artifact_metadata(payload, lock)
