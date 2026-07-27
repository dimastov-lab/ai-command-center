"""Portfolio project-id -> local repository path mapping.

Portfolio task cards carry a `project` field (`AICC`, `PRODUCT`, `AIOS`,
`AICOS`, `INFRA`, ...) that names an external repository this app must launch
agents against — a functionally *independent* namespace from
`command_center.models.PROJECT_IDS` (the AI Command Center's own Kanban
projects). This module is the single place the repository-path mapping is
resolved, kept deliberately separate from `command_center.project_config`:
neither ever validates a project id against the other's table, and a
`load_repository_paths()` id is never required to appear in
`models.PROJECT_IDS` (e.g. `INFRA` has no Kanban counterpart) or vice versa
(`BANK`/`LEGAL`/`BUSINESS`/`PERSONAL` have no Portfolio repository mapping).

Since the AICC-AUDIT-001 remediation split `models.PROJECT_IDS` into distinct
`AICC`/`AIOS`/`AICOS`/`PRODUCT`/`ECOSYSTEM` ids, several of the *literal id
strings* now intentionally match between the two tables — by design, since
both name the same real project/product from two different angles (Portfolio:
"which repository to launch agents into"; Kanban: "which project a task
belongs to"), not a naming collision. The two tables still never cross-
reference each other.

Local, machine-specific, gitignored: `data/portfolio_config.json` (see
`.gitignore`), mirroring `project_config.py`'s own `project_config.json`
convention exactly, including reusing its path-validation function rather
than duplicating it.

Only `AICC` and `PRODUCT` ship a built-in default path (the two mappings the
operator already gave us). Every other project id — `AIOS`, `AICOS`,
`INFRA`, and any future one — has no default and must be configured
explicitly here before any of its cards can be launched: a path is never
guessed.
"""

from __future__ import annotations

from pathlib import Path

from command_center import storage
from command_center.project_config import validate_repository_path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = storage.resolve_data_dir(ROOT)
CONFIG_FILE = DATA_DIR / "portfolio_config.json"

DEFAULT_REPOSITORY_PATHS: dict[str, str] = {
    "AICC": str(Path.home() / "Projects" / "ai-command-center"),
    "PRODUCT": str(Path.home() / "Projects" / "_repos" / "aios-product"),
}


def _read_overrides() -> dict:
    overrides = storage.read_json(CONFIG_FILE, {})
    return overrides if isinstance(overrides, dict) else {}


def load_repository_paths() -> dict[str, str | None]:
    """Every known project id's configured repository path — the built-in
    defaults, overridden (or explicitly cleared, via a stored `null`) by
    whatever the operator has saved. Project ids with no default and no
    override are simply absent (never a guessed value)."""
    overrides = _read_overrides()
    mapping: dict[str, str | None] = dict(DEFAULT_REPOSITORY_PATHS)
    for project_id, path in overrides.items():
        if isinstance(path, str) and path.strip():
            mapping[project_id] = path
        else:
            mapping.pop(project_id, None)
    return mapping


def get_repository_path(project_id: str) -> str | None:
    return load_repository_paths().get(project_id)


def save_repository_path(project_id: str, repository_path: str | None) -> None:
    """`repository_path=None` (or empty) explicitly clears any override —
    including a built-in default, so an operator can deliberately unset
    `AICC`/`PRODUCT` too if this machine's checkout moves."""
    overrides = _read_overrides()
    overrides[project_id] = repository_path if repository_path else None
    storage.atomic_write_json(CONFIG_FILE, overrides)


__all__ = [
    "DEFAULT_REPOSITORY_PATHS",
    "load_repository_paths",
    "get_repository_path",
    "save_repository_path",
    "validate_repository_path",
]
