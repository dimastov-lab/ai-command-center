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

from command_center import git_info, models, storage

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = storage.resolve_data_dir(ROOT)
CONFIG_FILE = DATA_DIR / "project_config.json"
CONFIG_EXAMPLE_FILE = DATA_DIR / "project_config.example.json"

# Engineering-environment defaults a new task inherits from its project (see
# `task_defaults_from_project`), plus the descriptive/planning fields the
# Projects settings UI exposes. All are optional overrides layered onto
# `default_project_config` by `load_project_configs` — a project missing any
# of these in `project_config.json` just keeps the built-in default below,
# which is how pre-existing project configs (only `repository_path`/
# `default_workspace_path`) keep loading unchanged.
OVERRIDABLE_FIELDS: list[str] = [
    "repository_path",
    "default_workspace_path",
    "default_branch",
    "default_executor",
    "default_prompt",
    "description",
    "status",
    "priority",
    "progress",
    "current_sprint",
    "current_milestone",
    "owner",
]

PROJECT_STATUSES: list[str] = ["Planning", "Active", "Paused", "Blocked", "Done"]

# Same value set as `app.py`'s task-level `PRIORITIES` — kept as an independent
# constant here (rather than imported from `app.py`, which must never be
# imported by a `command_center` module) since a project's priority and a
# task's priority are conceptually separate fields that merely happen to
# share a vocabulary today.
PROJECT_PRIORITIES: list[str] = ["Low", "Medium", "High", "Critical"]

DISPLAY_NAMES: dict[str, str] = {
    "AICC": "AI Command Center",
    "AIOS": "AIOS",
    "AICOS": "AICOS",
    "PRODUCT": "AIOS Product",
    "ECOSYSTEM": "Ecosystem",
    "BANK": "Bank Strategy",
    "LEGAL": "Legal",
    "BUSINESS": "Business",
    "PERSONAL": "Personal",
}

# Relative to ROOT. Empty/missing entries just mean "no dedicated file yet" — the UI
# handles that gracefully rather than failing, matching the v1.1 behavior.
#
# Every key in `models.PROJECT_IDS` must appear here, even if the file doesn't
# exist on disk yet (AICC/AICOS/PRODUCT/ECOSYSTEM have none today) — this dict is
# consulted with `.get(project_id, ...)` everywhere, so a missing *entry* silently
# degrades to a generic guess instead of surfacing the gap. Omitting AICOS entirely
# was the root cause of a real bug: `app.py` used to keep its own second,
# hand-maintained project dict (not this one) that dropped AICOS from every
# project selector/filter in the app. See `models.PROJECT_IDS` for the single
# canonical id list `app.py` must iterate instead.
PROJECT_STATUS_FILES: dict[str, str] = {
    "AICC": "projects/AICC.md",
    "AIOS": "projects/AIOS.md",
    "AICOS": "projects/AICOS.md",
    "PRODUCT": "projects/PRODUCT.md",
    "ECOSYSTEM": "projects/ECOSYSTEM.md",
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
        # Optional per-project override consulted by `command_center.launch.
        # resolve_workspace_path` between a task's own `workspace_path` and the
        # `repository_path` fallback below. `None` unless explicitly set in
        # `project_config.json` — no existing project config needs to define it.
        "default_workspace_path": None,
        # Engineering-environment defaults a new task inherits at creation time
        # (see `task_defaults_from_project`) — same "unset unless explicitly
        # configured" contract as `default_workspace_path` above.
        "default_branch": None,
        "default_executor": None,
        "default_prompt": "",
        # Descriptive/planning fields surfaced in the Projects settings UI.
        # Purely informational — nothing in Launch or task inheritance reads
        # these — so their defaults are just sensible blanks.
        "description": "",
        "status": PROJECT_STATUSES[1],  # "Active"
        "priority": PROJECT_PRIORITIES[1],  # "Medium"
        "progress": 0,
        "current_sprint": None,
        "current_milestone": None,
        "owner": "",
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
        if isinstance(override, dict):
            for field in OVERRIDABLE_FIELDS:
                if override.get(field) not in (None, ""):
                    cfg[field] = override[field]
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


# --------------------------------------------------------------------------
# Project name normalization (task import)
# --------------------------------------------------------------------------

# Founder-authored task packages (see `command_center.task_import`) refer to
# projects by free-text names, not by `models.PROJECT_IDS`. This table is the
# single, explicit mapping from every name a package is allowed to use onto a
# canonical id already registered in `models.PROJECT_IDS` — it is deliberately
# not a fallback/default: a name absent from this table fails validation in
# `task_import.validate_task_package` rather than being silently assigned to
# a guessed project.
#
# Founder Review (AICC-AUDIT-001 remediation) rejected an earlier version of
# this table that folded "AI Command Center"/"Ecosystem" into AICOS and
# "AIOS Product" into AIOS — collapsing genuinely distinct entities into one
# id made their tasks, filters, and metrics indistinguishable in the Kanban
# board. Every package name below now maps to its own canonical id, never to
# another project's id:
#
#   "AI Command Center" / "AICC"   -> AICC       (this repository/product)
#   "AIOS"                         -> AIOS       (unchanged, already canonical)
#   "AICOS"                        -> AICOS      (unchanged, already canonical)
#   "AIOS Product" / "PRODUCT"     -> PRODUCT    (AIOS's commercial/product layer — a
#                                                  distinct deliverable from AIOS core,
#                                                  tracked separately on its own board)
#   "Ecosystem" / "ECOSYSTEM"      -> ECOSYSTEM  (cross-project gate tasks that span
#                                                  AICC/AIOS/AICOS/PRODUCT — belong to
#                                                  none of them individually)
PROJECT_NAME_ALIASES: dict[str, str] = {
    "ai command center": "AICC",
    "aicc": "AICC",
    "aios": "AIOS",
    "aicos": "AICOS",
    "aios product": "PRODUCT",
    "product": "PRODUCT",
    "ecosystem": "ECOSYSTEM",
}


def normalize_project_id(raw: str | None) -> str | None:
    """Resolve a task package's free-text `project` field to a canonical
    `models.PROJECT_IDS` entry, or `None` if it cannot be resolved.

    Already-canonical ids pass through unchanged; every other supported name
    is looked up case/whitespace-insensitively in `PROJECT_NAME_ALIASES`.
    Returning `None` on a miss (rather than guessing a default project) is
    deliberate — see the module-level note above.
    """
    if not raw or not raw.strip():
        return None
    if raw in models.PROJECT_IDS:
        return raw
    key = " ".join(raw.strip().lower().split())
    return PROJECT_NAME_ALIASES.get(key)


def save_project_settings(project_id: str, **fields: object) -> None:
    """Generic setter for any subset of `OVERRIDABLE_FIELDS` — the Projects
    settings UI's single save action for Default Workspace/Branch/Executor/
    Prompt/Description/Status/Priority/Progress/Sprint/Milestone/Owner.

    `save_repository_path` above is kept as its own dedicated function (its
    existing call sites and tests are untouched); this is the superset used
    by every other field. A field value of `None` or `""` clears the override
    (falls back to `default_project_config`'s default), matching
    `save_repository_path`'s existing clear-on-falsy contract.
    """
    if project_id not in models.PROJECT_IDS:
        raise ValueError(f"Unknown project: {project_id}")
    unknown = set(fields) - set(OVERRIDABLE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown project config field(s): {sorted(unknown)}")

    overrides = _read_overrides()
    entry = overrides.get(project_id)
    entry = dict(entry) if isinstance(entry, dict) else {}
    for key, value in fields.items():
        if value in (None, ""):
            entry.pop(key, None)
        else:
            entry[key] = value
    overrides[project_id] = entry
    storage.atomic_write_json(CONFIG_FILE, overrides)


def task_defaults_from_project(cfg: dict) -> dict:
    """Fields a new task inherits from its project's configuration at creation
    time (see `tasks_repository.new_task_record`'s `workspace_path`/`branch`/
    `executor`/`prompt` kwargs). The user is never required to enter these
    manually — the Create Task UI pre-fills them from here and only overrides
    what the user explicitly changes.

    `workspace_path` mirrors `command_center.launch.resolve_workspace_path`'s
    precedence (project default workspace, else repository path) so the value
    baked onto the task at creation is exactly what Launch would already have
    resolved for it — this function does not change Launch's own fallback
    chain, it just materializes the same result earlier, for display and for
    legacy tasks created without ever visiting Launch.
    """
    return {
        "workspace_path": cfg.get("default_workspace_path") or cfg.get("repository_path") or None,
        "branch": cfg.get("default_branch") or None,
        "executor": cfg.get("default_executor") or None,
        "prompt": cfg.get("default_prompt") or "",
    }


def validate_project_settings(cfg: dict) -> list[str]:
    """Non-fatal warnings for the Projects settings UI to show before saving.

    Read-only — never mutates the filesystem or git state — and never blocks
    a save; the caller always persists the values via `save_project_settings`
    regardless of what is returned here, the same tolerance the pre-existing
    repository-path field already has (an unconfigured/invalid path is only
    ever caught later, by Launch's own `validate_launch`).
    """
    warnings: list[str] = []

    repository_path = cfg.get("repository_path")
    if repository_path:
        ok, message = validate_repository_path(repository_path)
        if not ok:
            warnings.append(f"Путь к репозиторию: {message}")

    workspace_path = cfg.get("default_workspace_path")
    if workspace_path:
        ok, message = validate_repository_path(workspace_path)
        if not ok:
            warnings.append(f"Workspace по умолчанию: {message}")
        else:
            status = git_info.get_status(Path(workspace_path).expanduser())
            if not status.get("is_repo"):
                warnings.append(f"Workspace по умолчанию не является git-репозиторием: {workspace_path}")
            else:
                branch = cfg.get("default_branch")
                if branch:
                    branches = git_info.get_branches(Path(workspace_path).expanduser())
                    if branches and branch not in branches:
                        warnings.append(f"Ветка «{branch}» не найдена в workspace по умолчанию.")

    executor_id = cfg.get("default_executor")
    if executor_id:
        # Imported locally: `executors` imports `agent_runner`, which imports
        # this module — a module-level import here would be circular.
        from command_center import executors

        if executor_id not in executors.EXECUTOR_IDS:
            warnings.append(f"Неизвестный executor: {executor_id}")
        elif not executors.get_executor(executor_id).available:
            warnings.append(f"Executor «{executor_id}» зарегистрирован, но пока недоступен для запуска.")

    prompt = cfg.get("default_prompt") or ""
    if len(prompt) > 20000:
        warnings.append("Prompt по умолчанию длиннее 20000 символов — проверьте содержимое.")

    return warnings
