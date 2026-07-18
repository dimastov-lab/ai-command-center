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


@dataclass
class LaunchValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    git_status: dict[str, object] | None = None

    @property
    def can_launch(self) -> bool:
        return not self.errors

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.warnings)


def validate_launch(*, workspace_path: str | None, expected_branch: str | None = None) -> LaunchValidation:
    """Read-only pre-flight check. Never mutates the repository or filesystem."""
    result = LaunchValidation()

    if not workspace_path:
        result.errors.append("Workspace-путь не настроен для этой задачи.")
        return result

    path = Path(workspace_path)
    if not path.exists() or not path.is_dir():
        result.errors.append(f"Workspace не найден на диске: {workspace_path}")
        return result

    status = git_info.get_status(path)
    result.git_status = status

    if not status.get("is_repo"):
        result.errors.append(f"{workspace_path} не является git-репозиторием.")
        return result

    branch = status.get("branch")
    if branch == "(detached HEAD)":
        result.warnings.append("Репозиторий в состоянии detached HEAD.")

    if status.get("dirty"):
        modified = status.get("modified_count", 0)
        untracked = status.get("untracked_count", 0)
        result.warnings.append(
            f"Рабочее дерево не чистое: {modified} изменённых, {untracked} неотслеживаемых файлов."
        )

    if expected_branch and branch and branch != "(detached HEAD)" and branch != expected_branch:
        result.warnings.append(f"Текущая ветка «{branch}» отличается от ожидаемой «{expected_branch}».")

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
    `launch_history` even once acknowledged."""
    branch = (validation.git_status or {}).get("branch") if validation.git_status else None
    status = "Requires Attention" if validation.warnings else "Launching"
    models.append_launch_attempt(
        task, executor=executor_id, branch=branch, status=status, warnings=list(validation.warnings)
    )
    if not task.get("started_at"):
        task["started_at"] = models.iso_now()
    models.append_timeline_event(task, "workspace_opened", f"Запуск начат ({executor_id}).")
    models.set_current_stage(task, "Workspace Verified")
    return status


def complete_launch(task: dict, *, executor_id: str, succeeded: bool) -> str:
    branch = task.get("branch")
    status = "Completed" if succeeded else "Failed"
    models.append_launch_attempt(task, executor=executor_id, branch=branch, status=status)
    task["finished_at"] = models.iso_now()
    event_type = "completed" if succeeded else "launch_requires_attention"
    models.append_timeline_event(task, event_type, f"Запуск завершён со статусом {status}.")
    return status
