"""Artifact/report discovery helpers — plain Python, no Streamlit dependency.

Extracted verbatim from `app.py` (`list_markdown_files`, `project_from_path`,
`infer_task_type_from_filename`, and the `TASK_TYPES` enum they depend on) so
that Streamlit-free callers — in particular `command_center.workspace_home` —
can discover generated artifacts and reports without importing `app.py` and,
as a side effect, initializing Streamlit. This module has no dependency on
`app.py`, `command_center.workspace_home`, or `command_center.runtime.*`; it
is a leaf module (stdlib `pathlib` only).
"""

from __future__ import annotations

from pathlib import Path

TASK_TYPES: list[str] = [
    "implementation",
    "review",
    "remediation",
    "final_gate",
    "architecture_review",
]


def list_markdown_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        (path for path in directory.rglob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def project_from_path(path: Path, base: Path) -> str:
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return "—"
    return parts[0] if len(parts) > 1 else "—"


def infer_task_type_from_filename(path: Path) -> str | None:
    parts = path.stem.split("_", 1)
    if len(parts) == 2 and parts[1] in TASK_TYPES:
        return parts[1]
    return None


def read_text(path: Path) -> str:
    """Best-effort raw file read for a discovered artifact/report, returning
    `""` on a missing file or read error rather than raising — callers (in
    particular `command_center.workspace_home`) treat an unreadable file the
    same as an empty one rather than failing the whole snapshot build."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
