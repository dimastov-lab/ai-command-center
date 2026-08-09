"""Fetch and verify the exact accepted AIOS SDK artifact (no mutable fallback)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "aios-sdk.lock.json"
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024


class ArtifactError(RuntimeError):
    pass


class _CredentialSafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


@dataclass(frozen=True)
class ArtifactLock:
    repository: str
    source_sha: str
    accepted_main_sha: str
    run_id: int
    artifact_id: int
    artifact_name: str
    wheel_filename: str
    wheel_sha256: str
    version: str
    api_major: int

    def as_dict(self) -> dict[str, object]:
        return dict(vars(self))

    def with_wheel_sha256(self, value: str) -> ArtifactLock:
        return replace(self, wheel_sha256=value)


def load_lock(path: Path = LOCK_PATH) -> ArtifactLock:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lock = ArtifactLock(**payload)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise ArtifactError("invalid AIOS SDK lock") from error
    if (
        len(lock.source_sha) != 40
        or len(lock.accepted_main_sha) != 40
        or len(lock.wheel_sha256) != 64
        or not all(value > 0 for value in (lock.run_id, lock.artifact_id, lock.api_major))
    ):
        raise ArtifactError("invalid AIOS SDK lock identity")
    return lock


def extract_verified_artifact(data: bytes, output: Path, lock: ArtifactLock) -> Path:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            allowed = {lock.wheel_filename, "SHA256SUMS"}
            if set(names) != allowed or any(
                PurePosixPath(name).name != name for name in names
            ):
                raise ArtifactError("artifact contains unexpected files")
            wheel = archive.read(lock.wheel_filename)
            manifest = archive.read("SHA256SUMS").decode("ascii")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as error:
        raise ArtifactError("invalid artifact archive") from error
    digest = hashlib.sha256(wheel).hexdigest()
    if manifest.strip() != f"{digest}  {lock.wheel_filename}":
        raise ArtifactError("artifact manifest checksum mismatch")
    if digest != lock.wheel_sha256:
        raise ArtifactError("locked wheel checksum mismatch")
    output.mkdir(parents=True, exist_ok=True)
    target = output / lock.wheel_filename
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(wheel)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ArtifactError("cannot persist verified SDK wheel") from error
    return target


def validate_artifact_metadata(payload: object, lock: ArtifactLock) -> None:
    if not isinstance(payload, dict):
        raise ArtifactError("invalid artifact metadata")
    workflow = payload.get("workflow_run")
    if (
        payload.get("id") != lock.artifact_id
        or payload.get("name") != lock.artifact_name
        or payload.get("expired") is not False
        or not isinstance(workflow, dict)
        or workflow.get("id") != lock.run_id
        or workflow.get("head_sha") != lock.accepted_main_sha
    ):
        raise ArtifactError("artifact metadata identity mismatch")


def _read(request: Request, limit: int) -> bytes:
    try:
        with build_opener(_CredentialSafeRedirect()).open(request, timeout=30) as response:
            data = response.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ArtifactError("AIOS SDK artifact download failed") from error
    if len(data) > limit:
        raise ArtifactError("AIOS SDK artifact exceeds size limit")
    return data


def fetch_artifact(lock: ArtifactLock, token: str) -> bytes:
    if not token:
        raise ArtifactError("AIOS_ARTIFACT_READ_TOKEN is required")
    root = f"https://api.github.com/repos/{lock.repository}/actions/artifacts/{lock.artifact_id}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        metadata = json.loads(_read(Request(root, headers=headers), MAX_METADATA_BYTES))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ArtifactError("invalid artifact metadata") from error
    validate_artifact_metadata(metadata, lock)
    return _read(Request(f"{root}/zip", headers=headers), MAX_ARTIFACT_BYTES)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = load_lock()
    data = fetch_artifact(lock, os.environ.get("AIOS_ARTIFACT_READ_TOKEN", ""))
    path = extract_verified_artifact(data, args.output, lock)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
