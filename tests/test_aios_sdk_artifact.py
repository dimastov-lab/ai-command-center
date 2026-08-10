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
    resolve_artifact_token,
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
    assert lock.source_sha == "5cc29a11294011eee1d87f1de7ce284cde9cb94e"
    assert lock.accepted_main_sha == "5cc29a11294011eee1d87f1de7ce284cde9cb94e"
    assert lock.run_id == 31329805298
    assert lock.artifact_id == 9042593332
    assert lock.wheel_sha256 == "3ca9c713eb99cb74a6cc93f2e441174e5e214925b324d1eca1c02c71500da680"
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


def test_artifact_extracts_with_release_archive_extra_files(tmp_path):
    lock = load_lock()
    wheel = b"wheel-bytes"
    checksum = hashlib.sha256(wheel).hexdigest()
    test_lock = lock.with_wheel_sha256(checksum)

    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(lock.wheel_filename, wheel)
            archive.writestr("SHA256SUMS", f"{checksum}  {lock.wheel_filename}\n")
            archive.writestr("release-manifest.json", "{}")
        full_archive = buffer.getvalue()

    path = extract_verified_artifact(full_archive, tmp_path, test_lock)
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
    payload["workflow_run"]["head_sha"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with pytest.raises(ArtifactError, match="identity"):
        validate_artifact_metadata(payload, lock)


def test_artifact_metadata_allows_missing_workflow_run_when_name_matches_accept_head_sha():
    lock = load_lock()
    payload = {
        "id": lock.artifact_id,
        "name": lock.artifact_name,
        "expired": False,
    }
    validate_artifact_metadata(payload, lock)


def test_resolve_artifact_token_prefers_readonly_then_legacy():
    assert resolve_artifact_token({"AIOS_ARTIFACT_READONLY_TOKEN": "new", "AIOS_ARTIFACT_READ_TOKEN": "old"}) == "new"
    assert resolve_artifact_token({"AIOS_ARTIFACT_READ_TOKEN": "old"}) == "old"


def test_resolve_artifact_token_requires_one_of_envs():
    with pytest.raises(ArtifactError, match="AIOS_ARTIFACT_READONLY_TOKEN or AIOS_ARTIFACT_READ_TOKEN"):
        resolve_artifact_token({})
