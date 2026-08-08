"""Worktree-aware launch validation and launch/permission profiles for the
Command Center roadmap launcher (task AICC-LAUNCH-001).

This is the single, executor-agnostic pre-flight validator that answers one
question about a roadmap-selected worktree: *"is this exact directory a safe,
registered worktree of the mapped repository, on the branch we expect, in a
state we can launch an agent into, right now?"* — reporting every failure mode
distinctly (workspace missing, not a git repository, corrupted/inaccessible git
metadata, not a *registered* worktree, detached HEAD, wrong branch, dirty tree)
rather than collapsing them into one opaque "can't launch" boolean.

Why a new validator rather than reusing `command_center.launch.validate_launch`:
that one (the plain Kanban launcher's) checks only "path exists / is a git repo
/ branch warning / dirty warning" — it has *no* concept of a worktree at all,
so it cannot tell a genuine registered worktree of the mapped repository apart
from some unrelated directory that merely happens to be *a* git repo. The
roadmap launcher's contract ("uses the roadmap-selected worktree; never
silently falls back to the main repository; never launches against the wrong
worktree") needs the stronger, worktree-registration-aware check implemented
here.

Registered-worktree check: a directory is only accepted if it appears in the
mapped repository's own `git worktree list --porcelain` output (compared by
resolved path). This is strictly stronger than a `git rev-parse
--git-common-dir` equality check alone: a hand-built `.git` *file* pointing at
the right common dir, or a stale/pruned worktree entry, shares the common dir
but is not actually a live registered worktree — this catches that.

Read-only invariant (mirrors `command_center.launch`, enforced by an AST scan
in `tests/test_worktree_launcher.py`): every git subcommand reached from this
module is a read — routed through `command_center.git_info`'s already-reviewed
read-only helpers (`rev-parse`, `worktree list --porcelain`, `branch
--show-current`, `status --porcelain`). This module never constructs a `git`
argv itself, never runs a git-write subcommand, and never writes a file.

Launch/permission profiles: `describe_permission_profile` / `describe_launch_
profile` surface — as labeled, displayable data — exactly which executor and
which tool-permission model a run will actually get, derived from the same
`agent_runner.READ_ONLY_TASK_TYPES` / `READ_ONLY_ALLOWED_TOOLS` /
`GIT_WRITE_DISALLOWED_TOOLS` that both the synchronous (`agent_runner.build_
command`) and async (`runtime.supervisor.build_claude_command`) launch paths
enforce. They describe, they never decide: the enforcement stays where it
already is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from command_center import agent_runner, capabilities, executors, git_info

# Machine-readable status values for a single validation check — surfaced in
# the launcher UI (`command_center.ui.portfolio_panel`) so each requirement can
# be shown as an individually pass/fail row, not just an aggregate verdict.
CHECK_OK = "ok"
CHECK_WARNING = "warning"
CHECK_FAILED = "failed"
CHECK_SKIPPED = "skipped"

DETACHED_HEAD = "(detached HEAD)"  # `git_info.get_status`'s marker for a detached HEAD


@dataclass(frozen=True)
class WorktreeCheck:
    """One named pre-flight check and its outcome, for display and assertion.
    `name` is a stable machine key; `label` is the human-facing (Russian, to
    match the rest of this app's UI) description; `detail` is the specific
    reason, if any."""

    name: str
    label: str
    status: str  # one of CHECK_OK / CHECK_WARNING / CHECK_FAILED / CHECK_SKIPPED
    detail: str = ""


@dataclass
class WorktreeLaunchValidation:
    """Full result of validating a roadmap-selected worktree. `errors` block a
    launch outright; `warnings` require explicit confirmation but do not block.
    `checks` is the per-requirement breakdown for the UI."""

    repository_root: str | None
    worktree_path: str | None
    expected_branch: str | None
    actual_branch: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[WorktreeCheck] = field(default_factory=list)
    git_status: dict[str, object] | None = None

    @property
    def can_launch(self) -> bool:
        return not self.errors

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.warnings)


# --------------------------------------------------------------------------
# Individual, independently-testable checks
# --------------------------------------------------------------------------


def git_metadata_accessible(worktree_path: Path) -> tuple[bool, str]:
    """Whether `worktree_path`'s git metadata is actually readable — distinct
    from "is it a git repository at all". Returns `(ok, detail)`.

    A directory can look like a work tree (`rev-parse --is-inside-work-tree`
    succeeds, or a `.git` file/dir is present) yet have its underlying git
    metadata missing or unreadable — a pruned/moved linked worktree whose
    backing `.git/worktrees/<name>` administrative directory was deleted, a
    `.git` gitfile pointing at a path that no longer exists, or a corrupted
    common dir. This surfaces that as its own failure so the operator sees
    "git metadata inaccessible", not a misleading "not a git repository"."""
    common = git_info.run_git_command(worktree_path, ["rev-parse", "--git-common-dir"])
    if common is None or common.returncode != 0 or not common.stdout.strip():
        return False, "git-метаданные недоступны: `git rev-parse --git-common-dir` завершился с ошибкой"

    common_path = Path(common.stdout.strip())
    if not common_path.is_absolute():
        common_path = (worktree_path / common_path)
    if not common_path.exists():
        return False, f"git-метаданные недоступны: общий git-каталог не существует: {common_path}"

    # For a *linked* worktree, the per-worktree administrative git dir
    # (`--git-dir`, e.g. `<repo>/.git/worktrees/<name>`) must also exist — if
    # the worktree was pruned/removed from under us, the work tree directory
    # can survive on disk while this backing dir is gone.
    git_dir = git_info.run_git_command(worktree_path, ["rev-parse", "--git-dir"])
    if git_dir is None or git_dir.returncode != 0 or not git_dir.stdout.strip():
        return False, "git-метаданные недоступны: `git rev-parse --git-dir` завершился с ошибкой"
    git_dir_path = Path(git_dir.stdout.strip())
    if not git_dir_path.is_absolute():
        git_dir_path = (worktree_path / git_dir_path)
    if not git_dir_path.exists():
        return False, f"git-метаданные недоступны: git-каталог worktree не существует: {git_dir_path}"

    return True, ""


def registered_worktrees(repository_root: Path) -> list[Path]:
    """Resolved paths of every worktree `git worktree list --porcelain`
    reports for `repository_root` (the main working tree included). Resolved
    so a comparison is robust to symlinked ancestors (e.g. macOS `/tmp` ->
    `/private/tmp`)."""
    resolved: list[Path] = []
    for entry in git_info.get_worktrees(repository_root):
        raw = entry.get("path")
        if not raw:
            continue
        try:
            resolved.append(Path(raw).resolve())
        except OSError:
            continue
    return resolved


def is_registered_worktree(repository_root: Path, worktree_path: Path) -> bool:
    """True only if `worktree_path` is one of `repository_root`'s *registered*
    worktrees (per `git worktree list`), compared by resolved path. A directory
    that is a git repository but was never `git worktree add`-ed against this
    repository — or a different repository's worktree — returns False."""
    try:
        target = worktree_path.resolve()
    except OSError:
        return False
    return target in registered_worktrees(repository_root)


# --------------------------------------------------------------------------
# The composite validator
# --------------------------------------------------------------------------


def validate_worktree(
    *,
    repository_root: str | Path | None,
    worktree_path: str | Path | None,
    expected_branch: str | None = None,
    require_clean: bool = True,
) -> WorktreeLaunchValidation:
    """Read-only pre-flight validation of a roadmap-selected worktree. Never
    mutates the repository or the filesystem.

    Every failure mode is reported distinctly. `require_clean=True` (the
    default, and what every production caller passes — the roadmap launcher
    and `Supervisor._validate_dedicated_worktree`) makes a dirty tree a
    blocking error, so an agent always starts from a known-clean tree and its
    own diff is unambiguous. `require_clean=False` downgrades it to a
    confirmable warning; it is a test-only relaxation and no longer mirrors
    the plain Kanban launcher, which rejects a dirty tree too (see
    `launch.BLOCKING_REPOSITORY_STATE_CODES`).

    A branch mismatch, a detached HEAD, an unregistered worktree, inaccessible
    git metadata, a non-repository, and a missing/absent workspace are all
    blocking errors — collectively the "never launch against the wrong
    worktree / never silently fall back to the main repository" guarantee."""
    result = WorktreeLaunchValidation(
        repository_root=str(repository_root) if repository_root is not None else None,
        worktree_path=str(worktree_path) if worktree_path is not None else None,
        expected_branch=expected_branch,
    )

    # 1. Workspace present + exists + is a directory.
    if not worktree_path:
        result.errors.append("Worktree не выбран для запуска.")
        result.checks.append(WorktreeCheck("workspace", "Worktree выбран", CHECK_FAILED, "путь не задан"))
        return result
    path = Path(worktree_path)
    if not path.exists() or not path.is_dir():
        result.errors.append(f"Worktree не найден на диске: {worktree_path}")
        result.checks.append(
            WorktreeCheck("workspace", "Worktree существует на диске", CHECK_FAILED, str(worktree_path))
        )
        return result
    result.checks.append(WorktreeCheck("workspace", "Worktree существует на диске", CHECK_OK, str(path)))

    # 2. Is a git repository / work tree.
    status = git_info.get_status(path)
    result.git_status = status
    if not status.get("is_repo"):
        # Distinguish "this was never a git work tree" from "this *is* meant to
        # be a work tree but its git metadata is gone/inaccessible" — a pruned
        # or moved linked worktree keeps its `.git` gitfile marker on disk while
        # its backing admin dir is deleted, so `git rev-parse --show-toplevel`
        # fails just like it would for a plain directory. Reporting the metadata
        # failure distinctly (rather than the misleading "not a git repository")
        # is exactly the "Git metadata accessibility" requirement.
        if (path / ".git").exists():
            metadata_ok, metadata_detail = git_metadata_accessible(path)
            if not metadata_ok:
                result.errors.append(metadata_detail)
                result.checks.append(
                    WorktreeCheck("git_metadata", "Git-метаданные доступны", CHECK_FAILED, metadata_detail)
                )
                return result
        result.errors.append(f"{worktree_path} не является git-репозиторием.")
        result.checks.append(WorktreeCheck("git_repository", "Является git-репозиторием", CHECK_FAILED))
        return result
    result.checks.append(WorktreeCheck("git_repository", "Является git-репозиторием", CHECK_OK))

    # 3. Git metadata is actually accessible (not merely "looks like a repo").
    metadata_ok, metadata_detail = git_metadata_accessible(path)
    if not metadata_ok:
        result.errors.append(metadata_detail)
        result.checks.append(WorktreeCheck("git_metadata", "Git-метаданные доступны", CHECK_FAILED, metadata_detail))
        return result
    result.checks.append(WorktreeCheck("git_metadata", "Git-метаданные доступны", CHECK_OK))

    # 4. Registered worktree of the *mapped* repository — the core
    #    worktree-awareness guarantee. Skipped only if no repository root was
    #    provided at all (itself an error), never silently passed.
    if repository_root is None:
        result.errors.append("Репозиторий не сопоставлен — невозможно проверить регистрацию worktree.")
        result.checks.append(
            WorktreeCheck("registered_worktree", "Зарегистрированный worktree репозитория", CHECK_FAILED,
                          "репозиторий не задан")
        )
    else:
        repo_root = Path(repository_root)
        if not repo_root.is_dir() or not git_info.get_status(repo_root).get("is_repo"):
            result.errors.append(f"Сопоставленный репозиторий недоступен или не является git-репозиторием: {repo_root}")
            result.checks.append(
                WorktreeCheck("registered_worktree", "Зарегистрированный worktree репозитория", CHECK_FAILED,
                              f"репозиторий недоступен: {repo_root}")
            )
        elif not is_registered_worktree(repo_root, path):
            result.errors.append(
                f"Каталог не является зарегистрированным worktree репозитория «{repo_root}» "
                "(отсутствует в `git worktree list`)."
            )
            result.checks.append(
                WorktreeCheck("registered_worktree", "Зарегистрированный worktree репозитория", CHECK_FAILED,
                              "нет в git worktree list")
            )
        else:
            result.checks.append(
                WorktreeCheck("registered_worktree", "Зарегистрированный worktree репозитория", CHECK_OK, str(repo_root))
            )

    # 5. Branch / HEAD state.
    actual_branch = status.get("branch")
    result.actual_branch = actual_branch if isinstance(actual_branch, str) else None
    if actual_branch == DETACHED_HEAD:
        # Blocking regardless of whether a branch was expected. A detached
        # HEAD used to be a mere warning when `expected_branch` was None, so
        # the exact same unusable state — commits landing on no branch, lost
        # to the next checkout — passed or failed depending on whether the
        # *caller* happened to know which branch it wanted. The hazard is in
        # the repository, not in the caller's knowledge of it.
        detail = (
            f"Ожидалась ветка «{expected_branch}», но HEAD в состоянии detached"
            if expected_branch
            else "HEAD в состоянии detached"
        )
        result.errors.append(
            f"{detail} — запуск отклонён: коммиты агента не попадут ни в одну ветку. "
            "Переключитесь на ветку (git switch <branch>) и повторите запуск."
        )
        result.checks.append(
            WorktreeCheck("branch", "Ветка соответствует ожидаемой", CHECK_FAILED, "detached HEAD")
        )
    elif expected_branch and actual_branch and actual_branch != expected_branch:
        result.errors.append(
            f"Текущая ветка «{actual_branch}» не совпадает с ожидаемой «{expected_branch}» — "
            "запуск против неверного worktree."
        )
        result.checks.append(
            WorktreeCheck("branch", "Ветка соответствует ожидаемой", CHECK_FAILED,
                          f"{actual_branch} ≠ {expected_branch}")
        )
    elif expected_branch:
        result.checks.append(
            WorktreeCheck("branch", "Ветка соответствует ожидаемой", CHECK_OK, str(actual_branch))
        )
    else:
        result.checks.append(
            WorktreeCheck("branch", "Ветка определена", CHECK_OK, str(actual_branch or "—"))
        )

    # 6. Clean / dirty state.
    if status.get("dirty"):
        modified = status.get("modified_count", 0)
        untracked = status.get("untracked_count", 0)
        detail = f"{modified} изменённых, {untracked} неотслеживаемых"
        if require_clean:
            result.errors.append(f"Рабочее дерево worktree не чистое ({detail}).")
            result.checks.append(WorktreeCheck("clean", "Рабочее дерево чистое", CHECK_FAILED, detail))
        else:
            result.warnings.append(f"Рабочее дерево не чистое: {detail}.")
            result.checks.append(WorktreeCheck("clean", "Рабочее дерево чистое", CHECK_WARNING, detail))
    else:
        result.checks.append(WorktreeCheck("clean", "Рабочее дерево чистое", CHECK_OK))

    return result


# --------------------------------------------------------------------------
# Launch profile / permission profile — displayable description of what a run
# will actually get (executor + tool-permission model). Description only; the
# enforcement lives in agent_runner / runtime.supervisor.
# --------------------------------------------------------------------------

PROFILE_READ_ONLY = "read_only"
PROFILE_IMPLEMENTATION = "implementation"


@dataclass(frozen=True)
class PermissionProfile:
    """The tool-permission model a run will execute under — the same model
    enforced identically by `agent_runner.build_command` (synchronous path)
    and `runtime.supervisor.build_claude_command` (async v2 path)."""

    key: str
    label: str
    allowed_tools: tuple[str, ...] | None  # None => all tools except `disallowed_tools`
    disallowed_tools: tuple[str, ...]
    summary: str
    # Canonical executor-capability profile name (`capabilities.PROFILE_*`:
    # `READ_ONLY` / `WORKSPACE_WRITE`) this permission model corresponds to,
    # so the launcher can show the capability profile before launch. Defaulted
    # (empty) so any existing construction site stays valid.
    capability_profile: str = ""


@dataclass(frozen=True)
class LaunchProfile:
    """Executor + permission model bundle for a launch — the "Launch profile"
    and "Permission profile" the roadmap launcher UI displays."""

    executor_id: str
    executor_label: str
    executor_available: bool
    permission: PermissionProfile
    label: str


def describe_permission_profile(task_type: str) -> PermissionProfile:
    """Map a run's `task_type` to the tool-permission model it will get.

    Read-only task types (`agent_runner.READ_ONLY_TASK_TYPES`) receive a
    *replaced* tool set (`--tools Read,Grep,Glob`): Bash — and therefore every
    shell-reachable mutation — is simply absent, not merely denied. Every other
    task type keeps Bash (needed to run tests/linters) but has git-write
    subcommands denied via `--disallowedTools`
    (`agent_runner.GIT_WRITE_DISALLOWED_TOOLS`)."""
    if task_type in agent_runner.READ_ONLY_TASK_TYPES:
        return PermissionProfile(
            key=PROFILE_READ_ONLY,
            label="Read-only",
            allowed_tools=tuple(agent_runner.READ_ONLY_ALLOWED_TOOLS),
            disallowed_tools=(),
            summary="Доступны только " + ", ".join(agent_runner.READ_ONLY_ALLOWED_TOOLS)
            + " — Bash и запись файлов недоступны.",
            capability_profile=capabilities.PROFILE_READ_ONLY,
        )
    return PermissionProfile(
        key=PROFILE_IMPLEMENTATION,
        label="Implementation",
        allowed_tools=None,
        disallowed_tools=tuple(agent_runner.GIT_WRITE_DISALLOWED_TOOLS),
        summary="Bash доступен для тестов/линтеров; git-write подкоманды "
        f"({len(agent_runner.GIT_WRITE_DISALLOWED_TOOLS)}) заблокированы.",
        capability_profile=capabilities.PROFILE_WORKSPACE_WRITE,
    )


def describe_launch_profile(*, executor_id: str = "claude_code", task_type: str) -> LaunchProfile:
    """Combine the executor (`command_center.executors`) and the permission
    profile for `task_type` into the single bundle the roadmap launcher shows
    as "Launch profile" / "Permission profile"."""
    executor = executors.get_executor(executor_id)
    permission = describe_permission_profile(task_type)
    return LaunchProfile(
        executor_id=executor.id,
        executor_label=executor.label,
        executor_available=executor.available,
        permission=permission,
        label=f"{executor.label} · {permission.label}",
    )
