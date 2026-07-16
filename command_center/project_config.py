"""Project configuration: repository paths, sensitivity, allowed agents, context files.

Local, machine-specific configuration (currently: a project's repository path) is stored
in `data/project_config.json`, which is gitignored — see `.gitignore`. A tracked
`data/project_config.example.json` documents the shape without any personal absolute
path. Everything else (display name, sensitivity, context files, reports/generated
directories) is derived in code from the project id, matching the existing
`PROJECTS`/`CONTEXT_FILES` tables in `app.py`.

Repository paths are never invented. `discover_candidate_repository_path` only ever
returns a path that is independently verified to exist and to be a git repository on
this machine — and even then, it is surfaced as a *suggestion* in the settings UI that
the user must explicitly save. Nothing under this module writes a repository path
without an explicit call from the UI layer.
"""

from __future__ import annotations

from pathlib import Path

from command_center import models, storage

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = storage.resolve_data_dir(ROOT)
CONFIG_FILE = DATA_DIR / "project_config.json"
CONFIG_EXAMPLE_FILE = DATA_DIR / "project_config.example.json"

DISPLAY_NAMES: dict[str, str] = {
    "AIOS": "AIOS",
    "AICOS": "AICOS",
    "BANK": "Bank Strategy",
    "LEGAL": "Legal",
    "BUSINESS": "Business",
    "PERSONAL": "Personal",
}

# Relative to ROOT. Empty/missing entries just mean "no dedicated file yet" — the UI
# handles that gracefully rather than failing, matching the v1.1 behavior.
PROJECT_STATUS_FILES: dict[str, str] = {
    "AIOS": "projects/AIOS.md",
    "BANK": "projects/BANK_STRATEGY.md",
    "LEGAL": "projects/LEGAL.md",
    "BUSINESS": "projects/BUSINESS.md",
    "PERSONAL": "projects/PERSONAL.md",
}

CONTEXT_FILES: dict[str, str] = {
    "AIOS": "context/AIOS_CONTEXT.md",
    "BANK": "context/BANK_CONTEXT.md",
    "LEGAL": "context/LEGAL_CONTEXT.md",
}

GLOBAL_CONTEXT_FILES: list[str] = ["CURRENT_STATE.md", "DECISIONS.md"]

DEFAULT_ALLOWED_AGENTS: list[str] = ["claude_code"]


def default_project_config(project_id: str) -> dict:
    context_paths = [
        path
        for path in (PROJECT_STATUS_FILES.get(project_id), CONTEXT_FILES.get(project_id))
        if path
    ] + list(GLOBAL_CONTEXT_FILES)
    return {
        "id": project_id,
        "display_name": DISPLAY_NAMES.get(project_id, project_id),
        "repository_path": None,
        "allowed_agents": list(DEFAULT_ALLOWED_AGENTS),
        "sensitive": project_id in models.SENSITIVE_PROJECT_IDS,
        "context_file_paths": context_paths,
        "reports_dir": f"reports/{project_id}",
        "generated_dir": f"generated/{project_id}",
    }


def discover_candidate_repository_path(project_id: str) -> str | None:
    """Return a verified-existing git repository path for `project_id`, or None.

    This never guesses: the candidate must exist on disk *and* contain a `.git`
    directory before it is returned. Callers must still treat the result as a
    suggestion requiring explicit user confirmation, never as configuration.
    """
    candidates: dict[str, Path] = {
        "AIOS": Path.home() / "Projects" / "aios",
    }
    candidate = candidates.get(project_id)
    if candidate is None:
        return None
    if candidate.is_dir() and (candidate / ".git").is_dir():
        return str(candidate)
    return None


def _read_overrides() -> dict:
    overrides = storage.read_json(CONFIG_FILE, {})
    return overrides if isinstance(overrides, dict) else {}


def load_project_configs() -> dict[str, dict]:
    overrides = _read_overrides()
    configs: dict[str, dict] = {}
    for project_id in models.PROJECT_IDS:
        cfg = default_project_config(project_id)
        override = overrides.get(project_id)
        if isinstance(override, dict) and override.get("repository_path"):
            cfg["repository_path"] = override["repository_path"]
        configs[project_id] = cfg
    return configs


def get_project_config(project_id: str) -> dict:
    return load_project_configs().get(project_id, default_project_config(project_id))


def validate_repository_path(path_str: str) -> tuple[bool, str]:
    """Validate a candidate path before it may be saved as a project's repository path."""
    if not path_str or not path_str.strip():
        return False, "Путь не указан."
    path = Path(path_str.strip()).expanduser()
    if not path.is_absolute():
        return False, "Укажите абсолютный путь."
    if not path.exists():
        return False, f"Путь не существует: {path}"
    if not path.is_dir():
        return False, f"Путь не является директорией: {path}"
    return True, "OK"


def save_repository_path(project_id: str, repository_path: str | None) -> None:
    if project_id not in models.PROJECT_IDS:
        raise ValueError(f"Unknown project: {project_id}")
    overrides = _read_overrides()
    entry = overrides.get(project_id)
    entry = dict(entry) if isinstance(entry, dict) else {}
    if repository_path:
        entry["repository_path"] = repository_path
    else:
        entry.pop("repository_path", None)
    overrides[project_id] = entry
    storage.atomic_write_json(CONFIG_FILE, overrides)


def is_sensitive(project_id: str) -> bool:
    return project_id in models.SENSITIVE_PROJECT_IDS
