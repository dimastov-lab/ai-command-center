"""Launch System: pre-launch validation, local OS integration (terminal /
Finder / clipboard), and launch status + history bookkeeping for a task.

Hard invariant, enforced by `tests/test_launch.py`'s source-scan regression
test: this module never runs a git-write subcommand (merge, push, branch
delete/switch, rebase, commit, stash, reset, clean) and never closes or
merges a pull request. It only *reads* git state (via
`command_center.git_info`, itself read-only) and opens a terminal / Finder
window / copies text to the clipboard — nothing here is destructive,
mirrors an operator manually opening a folder, and is always reversible.

Branch switches, merges, and pushes remain something only the engineer (or
the launched agent, subject to `agent_runner`'s own tool restrictions) does
by hand — this module never does them automatically.

All OS-specific branching lives in `command_center.os_actions`, not here —
this module only calls into it, so it stays platform-agnostic itself (see
`docs/desktop/ARCHITECTURE.md` §6/§8.1: only one package may branch on
`sys.platform`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from command_center import git_info, models, os_actions

# `resolve_workspace_path`'s precedence tiers, in order. Surfaced in the Launch
# confirmation UI (`app.py`'s `render_agent_launcher`) and persisted on each
# `launch_history` entry so it's clear, after the fact, which tier actually
# produced the path a run executed against.
WORKSPACE_SOURCE_TASK = "task"
WORKSPACE_SOURCE_PROJECT_DEFAULT = "project_default"
WORKSPACE_SOURCE_REPOSITORY_FALLBACK = "repository_fallback"

WORKSPACE_SOURCE_LABELS: dict[str, str] = {
    WORKSPACE_SOURCE_TASK: "Задача (workspace_path)",
    WORKSPACE_SOURCE_PROJECT_DEFAULT: "Проект (default_workspace_path)",
    WORKSPACE_SOURCE_REPOSITORY_FALLBACK: "Проект (repository_path, fallback)",
}


@dataclass(frozen=True)
class WorkspaceSelection:
    path: str | None
    source: str  # one of WORKSPACE_SOURCE_*


def resolve_workspace_path(*, task: dict | None, project_config: dict | None) -> WorkspaceSelection:
    """Selects the single workspace path used consistently for every later
    launch step (validation, git reads, Terminal/Folder actions, the Claude
    launch itself, and persisted launch history) — never recomputed
    differently at different steps.

    Precedence, matching the Launch flow's documented resolution order:
    1. `task["workspace_path"]`, if explicitly set — even if it turns out to
       be invalid. A present-but-broken task workspace must block with a
       clear error (see `validate_launch`), never silently fall through to
       a lower tier: an engineer who pointed a task at the wrong path needs
       to see that, not have it masked by a fallback that happens to work.
    2. `project_config["default_workspace_path"]`, if configured.
    3. `project_config["repository_path"]` — the fallback every pre-existing
       task without a `workspace_path` already relied on, so legacy tasks
       keep launching unchanged with no data migration required.
    """
    task_workspace = (task or {}).get("workspace_path")
    if task_workspace:
        return WorkspaceSelection(path=task_workspace, source=WORKSPACE_SOURCE_TASK)

    project_default = (project_config or {}).get("default_workspace_path")
    if project_default:
        return WorkspaceSelection(path=project_default, source=WORKSPACE_SOURCE_PROJECT_DEFAULT)

    repository_path = (project_config or {}).get("repository_path")
    return WorkspaceSelection(path=repository_path, source=WORKSPACE_SOURCE_REPOSITORY_FALLBACK)


def resolve_expected_branch(*, task: dict | None, project_config: dict | None) -> str | None:
    """Precedence: `task["branch"]` → `project_config["default_branch"]` →
    `None`. Fixes the display gap where Expected Branch showed "—" even
    though the project's default branch was configured — the caller used to
    read only `task.get("branch")`, with no fallback at all."""
    task_branch = (task or {}).get("branch")
    if task_branch:
        return task_branch
    return (project_config or {}).get("default_branch") or None


def resolve_base_branch(*, task: dict | None, project_config: dict | None) -> str | None:
    """The branch a not-yet-existing feature/audit worktree should be created
    *from*. Precedence: `task["base_branch"]` → `project_config["default_branch"]`
    → `None`. Consumed by `command_center.workspace_provisioning` to run
    `git worktree add -b <expected_branch> <path> <base_branch>` when the
    workspace is absent — it is never the branch the agent runs on (that is
    `resolve_expected_branch`), only the point it forks from."""
    task_base = (task or {}).get("base_branch")
    if task_base:
        return task_base
    return (project_config or {}).get("default_branch") or None


# Stable, translation-independent classification codes for every validation
# issue. Callers (`launch_service.prepare_task_launch`, the Kanban launcher,
# the execution queue) branch on these — never on the human-facing, localized
# message text — so a distinct, *recoverable* pre-launch condition (a
# configured workspace that does not yet exist but can be provisioned) can be
# told apart from a genuinely fatal one without string-matching display text.
ISSUE_WORKSPACE_NOT_CONFIGURED = "workspace_not_configured"
ISSUE_WORKSPACE_MISSING = "workspace_missing"            # the one provisionable code
ISSUE_WORKSPACE_NOT_DIRECTORY = "workspace_not_directory"
ISSUE_WORKSPACE_NOT_GIT_REPO = "workspace_not_git_repo"
ISSUE_DETACHED_HEAD = "detached_head"
ISSUE_DIRTY_TREE = "dirty_tree"
ISSUE_BRANCH_MISMATCH = "branch_mismatch"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class LaunchIssue:
    """A single validation finding, carrying a stable `code` for programmatic
    branching alongside the localized `message` shown to the operator."""

    code: str
    message: str
    severity: str = SEVERITY_ERROR


@dataclass
class LaunchValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    git_status: dict[str, object] | None = None
    workspace_path: str | None = None
    issues: list[LaunchIssue] = field(default_factory=list)

    @property
    def can_launch(self) -> bool:
        return not self.errors

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.warnings)

    def _add_error(self, code: str, message: str) -> None:
        self.errors.append(message)
        self.issues.append(LaunchIssue(code=code, message=message, severity=SEVERITY_ERROR))

    def _add_warning(self, code: str, message: str) -> None:
        self.warnings.append(message)
        self.issues.append(LaunchIssue(code=code, message=message, severity=SEVERITY_WARNING))

    @property
    def error_codes(self) -> list[str]:
        return [i.code for i in self.issues if i.severity == SEVERITY_ERROR]

    def blocked_only_by(self, code: str) -> bool:
        """True when validation failed and *every* blocking error is `code` —
        the precise test for "the only thing wrong is a recoverable condition"
        (e.g. a missing-but-provisionable workspace), never inferred from
        message text."""
        codes = self.error_codes
        return bool(codes) and all(c == code for c in codes)


def validate_launch(*, workspace_path: str | None, expected_branch: str | None = None) -> LaunchValidation:
    """Read-only pre-flight check. Never mutates the repository or filesystem."""
    result = LaunchValidation(workspace_path=workspace_path)

    if not workspace_path:
        result._add_error(ISSUE_WORKSPACE_NOT_CONFIGURED, "Workspace-путь не настроен для этой задачи.")
        return result

    path = Path(workspace_path)
    if not path.exists():
        # Recoverable: a configured workspace that does not yet exist can be
        # provisioned as an isolated worktree (see
        # `launch_service.prepare_task_launch`). Distinct code from a path
        # that exists but is a file — that one is genuinely fatal.
        result._add_error(ISSUE_WORKSPACE_MISSING, f"Workspace не найден на диске: {workspace_path}")
        return result
    if not path.is_dir():
        result._add_error(ISSUE_WORKSPACE_NOT_DIRECTORY, f"Workspace не найден на диске: {workspace_path}")
        return result

    status = git_info.get_status(path)
    result.git_status = status

    if not status.get("is_repo"):
        result._add_error(ISSUE_WORKSPACE_NOT_GIT_REPO, f"{workspace_path} не является git-репозиторием.")
        return result

    branch = status.get("branch")
    if branch == "(detached HEAD)":
        result._add_warning(ISSUE_DETACHED_HEAD, "Репозиторий в состоянии detached HEAD.")

    if status.get("dirty"):
        modified = status.get("modified_count", 0)
        untracked = status.get("untracked_count", 0)
        result._add_warning(
            ISSUE_DIRTY_TREE,
            f"Рабочее дерево не чистое: {modified} изменённых, {untracked} неотслеживаемых файлов.",
        )

    if expected_branch and branch and branch != "(detached HEAD)" and branch != expected_branch:
        result._add_warning(
            ISSUE_BRANCH_MISMATCH,
            f"Текущая ветка «{branch}» отличается от ожидаемой «{expected_branch}».",
        )

    return result


def open_terminal_at(path: str | Path) -> tuple[bool, str]:
    return os_actions.open_terminal_at(path)


def open_folder_at(path: str | Path) -> tuple[bool, str]:
    return os_actions.reveal_in_file_manager(path)


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    return os_actions.copy_to_clipboard(text)


def begin_launch(task: dict, *, executor_id: str, validation: LaunchValidation) -> str:
    """Records a launch attempt and returns the resulting `launch_status`.
    A validation warning (dirty tree, detached HEAD, branch mismatch) — the
    caller must have already obtained explicit user confirmation before
    calling this — still lands as "Requires Attention" so it is visible in
    `launch_history` even once acknowledged. The recorded `workspace_path`
    is exactly the path `validation` was computed against — the same
    resolved path the caller is about to launch, never re-derived here."""
    branch = (validation.git_status or {}).get("branch") if validation.git_status else None
    status = "Requires Attention" if validation.warnings else "Launching"
    models.append_launch_attempt(
        task,
        executor=executor_id,
        branch=branch,
        status=status,
        warnings=list(validation.warnings),
        workspace_path=validation.workspace_path,
    )
    if not task.get("started_at"):
        task["started_at"] = models.iso_now()
    models.append_timeline_event(task, "workspace_opened", f"Запуск начат ({executor_id}).")
    models.set_current_stage(task, "Workspace Verified")
    return status


def complete_launch(
    task: dict, *, executor_id: str, succeeded: bool, workspace_path: str | None = None
) -> str:
    branch = task.get("branch")
    status = "Completed" if succeeded else "Failed"
    models.append_launch_attempt(
        task, executor=executor_id, branch=branch, status=status, workspace_path=workspace_path
    )
    task["finished_at"] = models.iso_now()
    event_type = "completed" if succeeded else "launch_requires_attention"
    models.append_timeline_event(task, event_type, f"Запуск завершён со статусом {status}.")
    return status
