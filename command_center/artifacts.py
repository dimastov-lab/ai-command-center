"""Artifact/report discovery — filesystem helpers with no Streamlit dependency.

Extracted verbatim (zero behavior change) from `app.py`'s original module-level
functions (`app.py:201-223`), which lived there only because nothing outside
`app.py` needed them until Workspace Home did — see `WORKSPACE_HOME_ARCHITECTURE.md`
§9/§9.1/§9.2 for the extraction rationale. This module is a leaf: it imports nothing
from `command_center.workspace_home`, `command_center.runtime.*`, or `app.py`, and it
never imports Streamlit. `app.py` and `command_center.workspace_home` both import
from here; the reverse must never happen.
"""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    if not path.exists():
        return "Файл пока не создан."
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Ошибка чтения файла: {exc}"
    return content if content.strip() else "Файл пока пуст."


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


# Recognized task types, mirrored from `app.py`'s `TASK_TYPES` — kept here as a
# private constant (not re-exported) so this module has no dependency on `app.py`.
_TASK_TYPES: frozenset[str] = frozenset(
    {"implementation", "review", "remediation", "final_gate", "architecture_review"}
)


def infer_task_type_from_filename(path: Path) -> str | None:
    parts = path.stem.split("_", 1)
    if len(parts) == 2 and parts[1] in _TASK_TYPES:
        return parts[1]
    return None
