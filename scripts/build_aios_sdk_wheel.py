#!/usr/bin/env python3
"""Build the vendored `aios-sdk` wheel from a pinned commit of the aios repo.

AICC's AIOS Tasks backend (`AICC_TASKS_BACKEND=aios`) and its test suite need
`aios_sdk`, which lives in the closed-core `aios` repository. That repository
is private, and CI referenced no secrets, so the SDK cannot be pip-installed
from a URL there. Instead this script packages *only* the SDK client
(`src/aios_sdk` — it imports nothing from `aios.*`) into a standalone wheel
committed under `vendor/`, which `requirements.txt` then references.

Sources are taken from `git archive <ref>` — committed content only, never the
working tree — so the wheel is attributable to one exact aios commit, which is
embedded in the version as a PEP 440 local segment (e.g. `0.1.0+gd3c69e4`).
Dependency bands, `requires-python`, author and license metadata are read from
the aios `pyproject.toml` at that same ref. The archive layout (fixed zip
timestamps, sorted entries, 0o644 mode) mirrors aios
`tools/build_distributions.py`, so rebuilding from the same ref is
byte-reproducible and a vendored-wheel diff means the pinned source changed.

Usage (from the AICC repository root):

    python scripts/build_aios_sdk_wheel.py [--aios-repo ../aios]
        [--ref origin/main] [--outdir vendor]
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

AICC_ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
DIST_NAME = "aios-sdk"
# The SDK's real third-party imports (see src/aios_sdk/*.py: httpx + pydantic).
# Bands are still sourced from the aios pyproject at the pinned ref; this list
# only selects which of the core's declared dependencies apply to the SDK.
SDK_DEPENDENCY_NAMES = {"httpx", "pydantic"}


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


def _archive_tree(repo: Path, ref: str) -> dict[str, bytes]:
    """Return {path: content} for the SDK sources at the committed ref."""
    tar_bytes = _git(
        repo, "archive", ref, "--", "src/aios_sdk", "LICENSE", "pyproject.toml"
    )
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or "__pycache__" in member.name:
                continue
            extracted = tar.extractfile(member)
            if extracted is not None:
                files[member.name] = extracted.read()
    return files


def _sdk_version(files: dict[str, bytes]) -> str:
    source = files["src/aios_sdk/_version.py"].decode()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
    if match is None:
        raise ValueError("cannot find __version__ in src/aios_sdk/_version.py")
    return match.group(1)


def _sdk_dependencies(project: dict) -> list[str]:
    selected = []
    for dependency in project.get("dependencies", []):
        name = re.split(r"[\s<>=!~\[;]", str(dependency), maxsplit=1)[0]
        if name.lower() in SDK_DEPENDENCY_NAMES:
            selected.append(str(dependency))
    missing = SDK_DEPENDENCY_NAMES - {
        re.split(r"[\s<>=!~\[;]", d, maxsplit=1)[0].lower() for d in selected
    }
    if missing:
        raise ValueError(f"aios pyproject no longer declares {sorted(missing)}")
    return selected


def _metadata(project: dict, version: str) -> bytes:
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {DIST_NAME}",
        f"Version: {version}",
        "Summary: AIOS Python SDK — standalone client wheel vendored for AICC",
    ]
    license_value = project.get("license")
    if isinstance(license_value, str) and license_value.strip():
        lines.append(f"License-Expression: {license_value.strip()}")
    lines.append("License-File: LICENSE")
    for author in project.get("authors", []):
        name = str(author.get("name", "")).strip()
        email = str(author.get("email", "")).strip()
        if name and email:
            lines.append(f"Author-email: {name} <{email}>")
    lines.append(f"Requires-Python: {project['requires-python']}")
    lines.extend(
        f"Requires-Dist: {dependency}" for dependency in _sdk_dependencies(project)
    )
    return ("\n".join(lines) + "\n").encode()


def _wheel_info() -> bytes:
    return (
        b"Wheel-Version: 1.0\n"
        b"Generator: aicc-aios-sdk-builder/1.0.0\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )


def _record_entry(path: str, content: bytes) -> tuple[str, str, str]:
    digest = (
        base64.urlsafe_b64encode(hashlib.sha256(content).digest())
        .rstrip(b"=")
        .decode()
    )
    return path, f"sha256={digest}", str(len(content))


def _zip_write(archive: zipfile.ZipFile, path: str, content: bytes) -> None:
    info = zipfile.ZipInfo(path, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, content)


def build(repo: Path, ref: str, outdir: Path) -> Path:
    files = _archive_tree(repo, ref)
    project = tomllib.loads(files["pyproject.toml"].decode())["project"]
    short_sha = _git(repo, "rev-parse", "--short=7", ref).decode().strip()
    version = f"{_sdk_version(files)}+g{short_sha}"
    normalized = DIST_NAME.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"

    entries: list[tuple[str, bytes]] = []
    for path in sorted(p for p in files if p.startswith("src/aios_sdk/")):
        entries.append((path.removeprefix("src/"), files[path]))
    entries.append((f"{dist_info}/METADATA", _metadata(project, version)))
    entries.append((f"{dist_info}/WHEEL", _wheel_info()))
    entries.append((f"{dist_info}/licenses/LICENSE", files["LICENSE"]))

    records = [_record_entry(path, content) for path, content in entries]
    record_path = f"{dist_info}/RECORD"
    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    writer.writerows([*records, (record_path, "", "")])
    entries.append((record_path, record_buffer.getvalue().encode()))

    outdir.mkdir(parents=True, exist_ok=True)
    wheel = outdir / f"{normalized}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, content in entries:
            _zip_write(archive, path, content)
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aios-repo", type=Path, default=AICC_ROOT.parent / "aios")
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--outdir", type=Path, default=AICC_ROOT / "vendor")
    arguments = parser.parse_args()
    wheel = build(arguments.aios_repo, arguments.ref, arguments.outdir)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    print(f"{wheel.relative_to(Path.cwd()) if wheel.is_relative_to(Path.cwd()) else wheel}")
    print(f"sha256={digest}")


if __name__ == "__main__":
    main()
