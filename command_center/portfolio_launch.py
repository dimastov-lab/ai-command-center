"""Dry-run planning and real launch of Portfolio task cards.

This module is the only place a Portfolio card ever turns into a git branch,
a git worktree, a generated agent prompt, an execution-queue entry, or a
running agent process. It deliberately does not reimplement any of those: it
calls straight into the existing `command_center.execution_queue` /
`command_center.launch_service` / `command_center.runtime` stack — the same
queue, the same launcher, the same SQLite workspace lock every other launch
path in this app already goes through (per
`command_center.runtime.db.create_run(..., enforce_workspace_lock=True)`,
reached via `execution_queue.launch_ready` ->
`launch_service.execute_agent_launch_v2` -> `ExecutionCenterAPI.start_run` ->
`Supervisor.start_raw`). The only genuinely new mutations this module
performs are `git worktree add` (and its rollback counterparts), since
nothing else in this codebase creates a worktree or a branch.

Branch/worktree resolution:

- A card's own `branch`/`worktree` fields are honored as an explicit override
  when non-blank (`branch_source`/`worktree_source` == `"card_override"` on
  the resulting `LaunchPlan`) — validated (a real git ref name; an absolute
  path), never silently discarded or silently replaced by a generated
  default. A blank/absent field falls back to the deterministic generated
  default (`task/<task_id lower-cased>` /
  `<worktrees_root>/<task_id lower-cased>`, `branch_source`/`worktree_source`
  == `"generated_default"`).
- If the resolved worktree path already exists on disk, this module *never*
  calls `git worktree add` against it (`worktree_mode == "existing"`):
  instead it validates the existing directory is a real worktree of the
  resolved repository, already on the resolved branch, and clean, then
  launches directly into it. Nothing this module didn't create is ever
  removed, reset, or have its branch deleted — see `_rollback_worktree`.
- If the resolved worktree path does not exist (`worktree_mode == "new"`),
  this module creates it. If the resolved branch already exists in the
  repository *and* came from an explicit card override, that is treated as
  "attach this existing branch to a new worktree" (`git worktree add <path>
  <branch>`, no `-b`, no base ref consumed) rather than a blocker — a
  generated-default branch already existing is still a hard blocker (it
  should never pre-exist; if it does, something wasn't cleaned up).

Safety model:

- `build_launch_plan`/`build_batch_plan` are pure and read-only: they inspect
  the filesystem and run only read-only git subcommands (`rev-parse`,
  `branch --list`, `check-ref-format`, `status --porcelain` via
  `command_center.git_info`), and never write anything — dry-run planning
  must produce zero mutation.
- Because both the generated branch and worktree names are deterministic per
  `task_id`, and this module never deletes a branch/worktree it created, git
  itself is the backstop against re-launching the same task twice under
  generated defaults (`git worktree add` fails outright if the path or
  branch already exists). For an explicit card override, the same backstop
  doesn't automatically hold (two different cards could each ask to reuse
  the same branch/worktree) — see `_find_conflicting_registration` and
  `_detect_batch_conflicts`, which check the resolved branch/worktree
  against every other task's registry entry / the rest of the same batch.
- A small local registry (`data/portfolio_launches.json`, gitignored) records
  every task once successfully launched, purely for fast duplicate rejection
  and UI history — never the sole authority (git's own state is), and never
  required to be perfectly race-free on its own: the exclusive-create lock
  file in `data/portfolio_locks/` (`os.open(..., O_CREAT | O_EXCL)`) is what
  actually serializes two concurrent launch attempts for the same task_id,
  the same technique this module needs because — unlike the SQLite-backed
  workspace lock in `runtime.db` — worktree/branch creation has no existing
  atomic primitive in this codebase to reuse.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from command_center import execution_queue, git_info, models, storage
from command_center.portfolio_models import PortfolioTask, unmet_requirements
from command_center.runtime import db as runtime_db

WORKTREES_ROOT_ENV = "AICC_PORTFOLIO_WORKTREES_ROOT"
DEFAULT_WORKTREES_ROOT = Path.home() / "Projects" / "worktrees"

REGISTRY_FILE_NAME = "portfolio_launches.json"
LOCKS_DIR_NAME = "portfolio_locks"

DEFAULT_MAX_CONCURRENT_LAUNCHES = 3

SOURCE_CARD_OVERRIDE = "card_override"
SOURCE_GENERATED_DEFAULT = "generated_default"

WORKTREE_MODE_EXISTING = "existing"
WORKTREE_MODE_NEW = "new"

# Card `type` values that map to a read-only agent role
# (`agent_runner.READ_ONLY_TASK_TYPES`). Everything else — including
# `implementation`/`design`/`documentation`/`infrastructure`/`audit`, none of
# which are read-only in practice for this Portfolio's own cards — maps to
# `implementation`, which keeps Bash available (needed to run the card's own
# `validation` commands) while still blocking git-write subcommands via
# `agent_runner.GIT_WRITE_DISALLOWED_TOOLS`.
_TASK_TYPE_MAP: dict[str, str] = {
    "review": "review",
    "final_gate": "final_gate",
    "architecture_review": "architecture_review",
}


def _map_task_type(card_type: str | None) -> str:
    return _TASK_TYPE_MAP.get(card_type or "", "implementation")


def worktrees_root() -> Path:
    override = os.environ.get(WORKTREES_ROOT_ENV, "").strip()
    return Path(override) if override else DEFAULT_WORKTREES_ROOT


def branch_name_for(task_id: str) -> str:
    return f"task/{task_id.lower()}"


def worktree_path_for(task_id: str) -> Path:
    return worktrees_root() / task_id.lower()


# --------------------------------------------------------------------------
# Branch / worktree resolution — card override vs. generated default
# --------------------------------------------------------------------------


def _validate_branch_name(name: str) -> str | None:
    """Returns an error message if `name` is not a safe git branch name,
    else `None`. Delegates to `git check-ref-format --branch`, which needs no
    repository context and does not touch the filesystem."""
    try:
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"не удалось проверить имя ветки: {exc}"
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "недопустимое имя ветки"
    return None


def resolve_branch(task: PortfolioTask) -> tuple[str | None, str, str | None]:
    """Returns `(resolved_branch, source, blocker)`.

    A `None`/absent card `branch` always resolves to a valid generated
    default (`blocker` is `None`). A present-but-blank (empty or
    whitespace-only) or invalid override yields `resolved_branch=None` and a
    descriptive `blocker` — it is never silently replaced by the generated
    default."""
    raw = task.branch
    if raw is None:
        default = branch_name_for(task.task_id) if task.task_id else None
        return default, SOURCE_GENERATED_DEFAULT, None

    stripped = raw.strip()
    if not stripped:
        return None, SOURCE_CARD_OVERRIDE, "поле branch в карточке пустое или состоит только из пробелов"

    error = _validate_branch_name(stripped)
    if error:
        return None, SOURCE_CARD_OVERRIDE, f"branch в карточке недопустим: {error}"
    return stripped, SOURCE_CARD_OVERRIDE, None


def resolve_worktree(task: PortfolioTask) -> tuple[Path | None, str, str | None]:
    """Returns `(resolved_worktree, source, blocker)` — same contract as
    `resolve_branch`. A relative override path is rejected as ambiguous
    (relative to what?) rather than silently interpreted."""
    raw = task.worktree
    if raw is None:
        default = worktree_path_for(task.task_id) if task.task_id else None
        return default, SOURCE_GENERATED_DEFAULT, None

    stripped = raw.strip()
    if not stripped:
        return None, SOURCE_CARD_OVERRIDE, "поле worktree в карточке пустое или состоит только из пробелов"

    candidate = Path(stripped).expanduser()
    if not candidate.is_absolute():
        return None, SOURCE_CARD_OVERRIDE, f"worktree в карточке должен быть абсолютным путём: {stripped}"
    return candidate.resolve(), SOURCE_CARD_OVERRIDE, None


def _git_common_dir(path: Path) -> Path | None:
    """The resolved `--git-common-dir` for `path` — identical for every
    worktree of the same repository, and how this module tells "an existing
    directory that happens to be *some* git repo" apart from "an existing
    worktree of *this specific* mapped repository"."""
    result = git_info.run_git_command(path, ["rev-parse", "--git-common-dir"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (path / common)
    try:
        return common.resolve()
    except OSError:
        return None


# --------------------------------------------------------------------------
# Registry (duplicate-launch bookkeeping) — data/portfolio_launches.json
# --------------------------------------------------------------------------


def _registry_file(root: Path) -> Path:
    return storage.resolve_data_dir(root) / REGISTRY_FILE_NAME


def load_registry(root: Path) -> dict[str, dict]:
    data = storage.read_json(_registry_file(root), {})
    return data if isinstance(data, dict) else {}


def save_registry(root: Path, registry: dict[str, dict]) -> None:
    storage.atomic_write_json(_registry_file(root), registry)


def _find_conflicting_registration(
    registry: dict[str, dict],
    *,
    branch: str | None,
    worktree: str | None,
    exclude_task_id: str | None,
) -> str | None:
    """The task_id of another already-registered launch that claims the same
    resolved `branch` or the same resolved `worktree`, if any — checked
    against the *resolved* values so a card-override collision between two
    different task ids is caught, not just a collision between two
    generated-default names (which git's own atomicity already prevents)."""
    for other_task_id, entry in registry.items():
        if other_task_id == exclude_task_id:
            continue
        if branch and entry.get("branch") == branch:
            return other_task_id
        if worktree and entry.get("worktree") == worktree:
            return other_task_id
    return None


def _locks_dir(root: Path) -> Path:
    return storage.resolve_data_dir(root) / LOCKS_DIR_NAME


def _claim(root: Path, task_id: str) -> bool:
    """Atomically claim `task_id` for launch. Returns False if another
    in-flight (or crashed-and-never-released) claim already exists —
    `release_stale_claim` is the explicit, operator-triggered way to clear a
    claim orphaned by a crash; this function itself never clears one, so two
    genuinely concurrent callers can never both proceed."""
    locks_dir = _locks_dir(root)
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{task_id}.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _release(root: Path, task_id: str) -> None:
    lock_path = _locks_dir(root) / f"{task_id}.lock"
    lock_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Dry-run planning
# --------------------------------------------------------------------------


@dataclass
class LaunchPlan:
    task_id: str
    project: str
    title: str | None
    source_path: str
    lane: str
    repository_root: str | None
    base_branch: str | None
    base_sha: str | None
    branch: str
    worktree: str
    task_type: str
    requested_branch: str | None = None
    branch_source: str = SOURCE_GENERATED_DEFAULT
    requested_worktree: str | None = None
    worktree_source: str = SOURCE_GENERATED_DEFAULT
    worktree_mode: str = WORKTREE_MODE_NEW
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def launchable(self) -> bool:
        return not self.blockers


def build_launch_plan(
    task: PortfolioTask,
    *,
    tasks_by_id: dict[str, PortfolioTask],
    repository_paths: dict[str, str | None],
    registry: dict[str, dict] | None = None,
) -> LaunchPlan:
    """Pure, read-only. Never mutates the filesystem, never runs a git-write
    subcommand — only `command_center.git_info`'s read-only helpers plus
    `git check-ref-format`/`rev-parse --git-common-dir` (both read-only)."""
    registry = registry or {}
    blockers: list[str] = []
    warnings: list[str] = []

    missing_fields = task.missing_required_fields()
    if missing_fields:
        blockers.append(f"в карточке отсутствуют обязательные поля: {', '.join(missing_fields)}")

    if task.lane != "ready":
        blockers.append(f"задача находится в статусе «{task.lane}», автозапуск разрешён только для «ready»")

    if task.task_id and task.task_id in registry:
        blockers.append("задача уже была запущена ранее — см. журнал запусков Portfolio")

    repo_root: Path | None = None
    repo_root_raw = repository_paths.get(task.project) if task.project else None
    if not repo_root_raw:
        blockers.append(f"репозиторий для проекта «{task.project}» не сопоставлен — настройте его в конфигурации")
    else:
        repo_root = Path(repo_root_raw).expanduser().resolve()
        if not repo_root.is_dir():
            blockers.append(f"путь репозитория не существует: {repo_root}")
        elif not (repo_root / ".git").exists():
            blockers.append(f"путь репозитория не является git-репозиторием: {repo_root}")

    resolved_branch, branch_source, branch_error = resolve_branch(task)
    if branch_error:
        blockers.append(branch_error)

    resolved_worktree, worktree_source, worktree_error = resolve_worktree(task)
    if worktree_error:
        blockers.append(worktree_error)

    branch = resolved_branch or ""
    worktree = str(resolved_worktree) if resolved_worktree else ""

    worktree_mode = WORKTREE_MODE_NEW
    base_branch = task.base_branch
    base_sha: str | None = None
    attach_existing_branch = False

    if resolved_worktree is not None and resolved_worktree.exists():
        worktree_mode = WORKTREE_MODE_EXISTING
        if not resolved_worktree.is_dir():
            blockers.append(f"существующий путь worktree не является директорией: {resolved_worktree}")
        else:
            status = git_info.get_status(resolved_worktree)
            if not status.get("is_repo"):
                blockers.append(f"существующий worktree не является git-репозиторием: {resolved_worktree}")
            else:
                if repo_root is not None:
                    existing_common = _git_common_dir(resolved_worktree)
                    canonical_common = _git_common_dir(repo_root)
                    if existing_common is None or canonical_common is None or existing_common != canonical_common:
                        blockers.append(
                            f"существующий worktree принадлежит другому репозиторию, а не сопоставленному «{repo_root}»"
                        )
                actual_branch = status.get("branch")
                if branch and actual_branch != branch:
                    blockers.append(
                        f"существующий worktree на ветке «{actual_branch}», ожидалась «{branch}»"
                    )
                if status.get("dirty"):
                    blockers.append(f"существующий worktree не чист (uncommitted changes): {resolved_worktree}")
            head_result = git_info.run_git_command(resolved_worktree, ["rev-parse", "HEAD"])
            base_sha = head_result.stdout.strip() if head_result and head_result.returncode == 0 else None
    else:
        if resolved_branch and repo_root is not None:
            existing_branches = git_info.get_branches(repo_root)
            if resolved_branch in existing_branches:
                if branch_source == SOURCE_CARD_OVERRIDE:
                    attach_existing_branch = True
                    warnings.append(
                        f"ветка «{resolved_branch}» уже существует — будет привязана к новому worktree "
                        "без создания новой ветки"
                    )
                else:
                    blockers.append(f"ветка уже существует: {resolved_branch}")

        if repo_root is not None:
            if not attach_existing_branch:
                if not base_branch:
                    blockers.append("в карточке не указана base_branch")
                else:
                    verify = git_info.run_git_command(repo_root, ["rev-parse", "--verify", f"{base_branch}^{{commit}}"])
                    if verify is None or verify.returncode != 0:
                        blockers.append(f"базовая ветка/ref не найдена в репозитории: {base_branch}")
                    else:
                        sha_result = git_info.run_git_command(repo_root, ["rev-parse", base_branch])
                        base_sha = sha_result.stdout.strip() if sha_result and sha_result.returncode == 0 else None
            elif base_branch:
                # Informational only in the attach case — `base_branch` is
                # not consumed by the `git worktree add <path> <branch>`
                # command that will actually run, so an invalid/missing
                # value here is never a blocker.
                verify = git_info.run_git_command(repo_root, ["rev-parse", "--verify", f"{base_branch}^{{commit}}"])
                if verify is not None and verify.returncode == 0:
                    sha_result = git_info.run_git_command(repo_root, ["rev-parse", base_branch])
                    base_sha = sha_result.stdout.strip() if sha_result and sha_result.returncode == 0 else None

    conflict_owner = _find_conflicting_registration(
        registry, branch=branch or None, worktree=worktree or None, exclude_task_id=task.task_id
    )
    if conflict_owner:
        blockers.append(f"конфликт: ветка/worktree уже используется другой запущенной задачей ({conflict_owner})")

    unmet = unmet_requirements(task, tasks_by_id)
    if unmet:
        blockers.append(f"не выполнены зависимости (requires): {', '.join(unmet)}")

    if task.conflicts_with:
        warnings.append(f"карточка объявляет conflicts_with: {', '.join(task.conflicts_with)} — проверьте вручную")
    if task.gated_by:
        warnings.append(f"карточка требует gated_by: {', '.join(task.gated_by)} — не проверяется автоматически")
    if task.autonomy and task.autonomy != "confirmed":
        warnings.append(f"autonomy карточки: «{task.autonomy}» (ожидалось «confirmed»)")

    return LaunchPlan(
        task_id=task.task_id or "",
        project=task.project or "",
        title=task.title,
        source_path=str(task.source_path),
        lane=task.lane,
        repository_root=str(repo_root) if repo_root else None,
        base_branch=base_branch,
        base_sha=base_sha,
        branch=branch,
        worktree=worktree,
        task_type=_map_task_type(task.type),
        requested_branch=task.branch,
        branch_source=branch_source,
        requested_worktree=task.worktree,
        worktree_source=worktree_source,
        worktree_mode=worktree_mode,
        blockers=blockers,
        warnings=warnings,
    )


def build_batch_plan(
    tasks: list[PortfolioTask],
    *,
    tasks_by_id: dict[str, PortfolioTask],
    repository_paths: dict[str, str | None],
    registry: dict[str, dict] | None = None,
) -> list[LaunchPlan]:
    registry = registry or {}
    return [
        build_launch_plan(task, tasks_by_id=tasks_by_id, repository_paths=repository_paths, registry=registry)
        for task in tasks
    ]


def _detect_batch_conflicts(plans: list[LaunchPlan]) -> dict[str, str]:
    """task_id -> blocking message for any task in `plans` whose *resolved*
    branch or worktree is also requested by another task in the same batch —
    caught before any mutation, since two tasks launched into the same
    branch/worktree would corrupt each other's work."""
    by_branch: dict[str, list[str]] = {}
    by_worktree: dict[str, list[str]] = {}
    for plan in plans:
        if plan.branch:
            by_branch.setdefault(plan.branch, []).append(plan.task_id)
        if plan.worktree:
            by_worktree.setdefault(plan.worktree, []).append(plan.task_id)

    conflicts: dict[str, str] = {}
    for branch, task_ids in by_branch.items():
        if len(task_ids) > 1:
            others = ", ".join(sorted(set(task_ids)))
            for task_id in task_ids:
                conflicts[task_id] = f"конфликт в пакете: несколько задач запрашивают одну и ту же ветку «{branch}» ({others})"
    for worktree, task_ids in by_worktree.items():
        if len(task_ids) > 1:
            others = ", ".join(sorted(set(task_ids)))
            message = f"конфликт в пакете: несколько задач запрашивают один и тот же worktree «{worktree}» ({others})"
            for task_id in task_ids:
                conflicts[task_id] = f"{conflicts[task_id]}; {message}" if task_id in conflicts else message
    return conflicts


# --------------------------------------------------------------------------
# Prompt generation
# --------------------------------------------------------------------------


def build_agent_prompt(task: PortfolioTask, plan: LaunchPlan) -> str:
    """The full instruction handed to the launched agent — the original card
    verbatim, every location/identity fact the agent needs, and the explicit
    guardrails required by the founder prompt (no other repos, no merge, no
    push, report format). The final-report section intentionally matches the
    headings `command_center.report_parser.parse_report` already recognizes
    (Verdict, Findings by severity, Files Modified/Created/Deleted, Commit
    Hash, Branch, Validation, Git Status, Recommended Next Action), so a
    Portfolio-launched run's report is parsed by the exact same, already-
    tested code path as every other run in this app."""
    deliverables = "\n".join(f"- {item}" for item in task.deliverables) or "- (не указано в карточке)"
    stop_conditions = "\n".join(f"- {item}" for item in task.stop_conditions) or "- (не указано в карточке)"
    validation_commands = "\n".join(task.validation) or "# в карточке не указаны команды проверки"

    if plan.worktree_mode == WORKTREE_MODE_EXISTING:
        worktree_note = (
            "Этот worktree УЖЕ СУЩЕСТВОВАЛ и был проверен AI Command Center перед запуском "
            "(верная ветка, тот же репозиторий, чистое рабочее дерево) — он не был создан этим запуском."
        )
        worktree_rules = (
            "- Worktree уже существовал до этого запуска. НЕ переключай ветку, НЕ выполняй `git reset`/"
            "`git clean`, НЕ изменяй состояние других worktree этого репозитория.\n"
            "- Сохраняй предшествующее состояние репозитория, кроме изменений, требуемых этой задачей."
        )
    else:
        worktree_note = "Этот worktree был создан AI Command Center специально для этого запуска."
        worktree_rules = (
            "- Worktree создан специально для этой задачи. НЕ изменяй другие worktree или репозитории."
        )

    return f"""# Portfolio Task: {task.task_id}

Ты выполняешь задачу, полученную из Portfolio через AI Command Center. Ниже —
полная исходная карточка задачи, за ней — параметры окружения выполнения и
обязательные ограничения.

## Исходная карточка (полностью, без изменений)

```markdown
{task.raw_text}
```

## Окружение выполнения

- Task ID: `{task.task_id}`
- Project: `{task.project}`
- Repository (базовый): `{plan.repository_root}`
- Worktree (рабочая директория — работай только здесь): `{plan.worktree}`
- Branch: `{plan.branch}`
- Base branch: `{plan.base_branch}`
- Base/HEAD commit SHA: `{plan.base_sha}`
- Worktree mode: `{plan.worktree_mode}` — {worktree_note}

## Acceptance criteria (deliverables)

{deliverables}

## Stop conditions

{stop_conditions}

## Validation commands

```bash
{validation_commands}
```

## Обязательные ограничения

- Работай ТОЛЬКО в указанном worktree ({plan.worktree}). Не изменяй файлы ни в
  каком другом репозитории или worktree на этой машине.
{worktree_rules}
- НЕ выполняй `git merge`, `git push`, не открывай и не сливай Pull Request.
- НЕ отмечай задачу выполненной в Portfolio и не перемещай карточку задачи.
- Не выходи за рамки объявленных deliverables/scope этой карточки.
- Commit разрешён только в рамках ветки `{plan.branch}`; никогда не коммить в другую ветку.

## Формат итогового отчёта

Заверши работу отчётом в следующем формате (используй эти заголовки
дословно):

```markdown
## Verdict

READY FOR COMMIT | APPROVED FOR COMMIT | NOT APPROVED FOR COMMIT | FAILED

## Findings

**Blocker:** ...
**High:** ...
**Medium:** ...
**Low:** ...

## Files Modified

- ...

## Files Created

- ...

## Files Deleted

- ...

Commit Hash: <hash или "нет">
Branch: {plan.branch}

## Validation

<вывод команд проверки выше>

## Git Status

<git status --porcelain>

## Recommended Next Action

<...>
```
"""


# --------------------------------------------------------------------------
# Git worktree/branch mutation (the only write git subcommands in this module)
# --------------------------------------------------------------------------


class PortfolioLaunchError(Exception):
    """Raised for any failure that must abort a launch before the agent is
    started — never raised after `execution_queue.launch_ready` has actually
    started a run."""


def _run_git_write(args: list[str], *, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def create_worktree(repo_root: Path, *, branch: str, worktree_path: Path, base_branch: str) -> None:
    """`git worktree add -b <branch> <path> <base_branch>` — a brand-new
    branch and a brand-new worktree. Only called when both need to be
    created (`worktree_mode == "new"`, no existing branch to attach)."""
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git_write(
        ["worktree", "add", "-b", branch, str(worktree_path), base_branch],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise PortfolioLaunchError(
            f"git worktree add failed: {(result.stderr or result.stdout).strip()}"
        )


def attach_worktree(repo_root: Path, *, branch: str, worktree_path: Path) -> None:
    """`git worktree add <path> <branch>` — attaches an already-existing
    branch (named by an explicit card override) to a brand-new worktree; no
    `-b`, no base ref consumed. Only the worktree is created here — the
    branch already existed and must never be deleted by this launch's
    rollback."""
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git_write(["worktree", "add", str(worktree_path), branch], cwd=repo_root)
    if result.returncode != 0:
        raise PortfolioLaunchError(
            f"git worktree add (existing branch) failed: {(result.stderr or result.stdout).strip()}"
        )


def remove_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Best-effort rollback — never raises. Only called for a worktree this
    launch attempt itself created (see `_rollback_worktree`)."""
    _run_git_write(["worktree", "remove", "--force", str(worktree_path)], cwd=repo_root)


def delete_branch(repo_root: Path, branch: str) -> None:
    """Best-effort rollback counterpart to `create_worktree` — never raises.
    Only called for a branch this launch attempt itself created (never for
    an attached-but-pre-existing branch, and never for an existing
    worktree's branch — see `_rollback_worktree`)."""
    _run_git_write(["branch", "-D", branch], cwd=repo_root)


def _rollback_worktree(
    repo_root: Path, worktree_path: Path, *, worktree_created: bool, branch: str, branch_created: bool
) -> None:
    """Removes only what this launch attempt itself created. An existing
    worktree (`worktree_mode == "existing"`) is never touched — neither
    `worktree_created` nor `branch_created` is ever `True` for one, by
    construction (see `launch_portfolio_task`). Attaching a pre-existing
    branch to a new worktree sets `worktree_created=True` but
    `branch_created=False`, so the worktree is removed but the branch that
    predates this launch survives."""
    if worktree_created:
        remove_worktree(repo_root, worktree_path)
    if branch_created:
        delete_branch(repo_root, branch)


# --------------------------------------------------------------------------
# Real launch
# --------------------------------------------------------------------------


@dataclass
class PortfolioLaunchResult:
    task_id: str
    launched: bool
    run_id: str | None = None
    message: str = ""
    plan: LaunchPlan | None = None


def _synthetic_task(task: PortfolioTask, plan: LaunchPlan, *, worktree_path: Path, prompt: str) -> dict:
    """A plain task dict shaped exactly like `command_center.tasks_repository`
    records, so it can be handed to `execution_queue`/`launch_service`
    unchanged — reusing that stack rather than a parallel one. Never
    persisted to `data/tasks.json`; it exists only for the duration of this
    one launch call."""
    record: dict = {
        "id": task.task_id,
        "project": task.project,
        "title": task.title or task.task_id,
        "status": "Ready",
        "depends_on": [],
    }
    models.normalize_task_workflow(record)
    models.normalize_task_execution(record)
    record["goal"] = task.title or task.task_id
    record["prompt"] = prompt
    record["workspace_path"] = str(worktree_path)
    record["branch"] = plan.branch
    record["executor"] = "claude_code"
    record["agent"] = "claude_code"
    record["task_type"] = plan.task_type
    return record


def launch_portfolio_task(
    root: Path,
    task: PortfolioTask,
    *,
    tasks_by_id: dict[str, PortfolioTask],
    repository_paths: dict[str, str | None],
    execution_center_api,
    confirmed: bool,
) -> PortfolioLaunchResult:
    """Launch exactly one Portfolio task. Requires `confirmed=True` — the
    caller's (UI's) explicit human confirmation; never launches on a plain
    render path.

    On any failure prior to the agent actually starting, this function rolls
    back whatever *it* created (worktree, branch, queue entry) — never an
    existing worktree/branch this launch reused — and never leaves a claim
    held, so a retry after fixing the reported problem is always possible.
    Once `execution_queue.launch_ready` reports the run started, nothing is
    rolled back regardless of what happens afterward (the run itself, and
    its own workspace lock, are the system of record from that point on)."""
    if not confirmed:
        raise PortfolioLaunchError("запуск требует явного подтверждения")

    registry = load_registry(root)
    plan = build_launch_plan(task, tasks_by_id=tasks_by_id, repository_paths=repository_paths, registry=registry)
    if not plan.launchable:
        return PortfolioLaunchResult(task_id=task.task_id or "", launched=False, message="; ".join(plan.blockers), plan=plan)

    # `plan.launchable` guarantees `task.task_id`/`task.project`/
    # `plan.repository_root`/`plan.branch`/`plan.worktree` are all present —
    # `build_launch_plan` adds a blocker for each of these otherwise —
    # narrowing them here (rather than threading `Optional` through every
    # call below) reflects that guarantee for the type checker too.
    assert task.task_id is not None
    assert task.project is not None
    assert plan.repository_root is not None
    assert plan.branch
    assert plan.worktree

    if not _claim(root, task.task_id):
        return PortfolioLaunchResult(
            task_id=task.task_id,
            launched=False,
            message="запуск для этой задачи уже выполняется или был выполнен параллельно",
            plan=plan,
        )

    repo_root = Path(plan.repository_root)
    worktree_path = Path(plan.worktree)
    branch_created = False
    worktree_created = False

    try:
        # Re-check duplicate/conflict state under the claim — closes the
        # race window between building `plan` above and holding the claim
        # (two concurrent callers could have both passed the earlier,
        # unlocked check).
        registry = load_registry(root)
        if task.task_id in registry:
            return PortfolioLaunchResult(
                task_id=task.task_id, launched=False, message="задача уже была запущена ранее", plan=plan
            )
        conflict_owner = _find_conflicting_registration(
            registry, branch=plan.branch, worktree=plan.worktree, exclude_task_id=task.task_id
        )
        if conflict_owner:
            return PortfolioLaunchResult(
                task_id=task.task_id,
                launched=False,
                message=f"конфликт: ветка/worktree уже используется задачей {conflict_owner}",
                plan=plan,
            )

        if plan.worktree_mode == WORKTREE_MODE_EXISTING:
            # Never call `git worktree add` here — re-validate the existing
            # worktree fresh (it could have changed since the dry-run plan
            # was built) and launch directly into it if it still checks out.
            status = git_info.get_status(worktree_path)
            if not worktree_path.is_dir() or not status.get("is_repo"):
                return PortfolioLaunchResult(
                    task_id=task.task_id, launched=False, message=f"существующий worktree недоступен: {worktree_path}", plan=plan
                )
            if status.get("branch") != plan.branch:
                return PortfolioLaunchResult(
                    task_id=task.task_id,
                    launched=False,
                    message=f"существующий worktree сменил ветку: ожидалась «{plan.branch}», сейчас «{status.get('branch')}»",
                    plan=plan,
                )
            if status.get("dirty"):
                return PortfolioLaunchResult(
                    task_id=task.task_id, launched=False, message=f"существующий worktree не чист: {worktree_path}", plan=plan
                )
        else:
            if worktree_path.exists():
                return PortfolioLaunchResult(
                    task_id=task.task_id, launched=False, message=f"путь worktree уже существует: {worktree_path}", plan=plan
                )
            branch_exists = plan.branch in git_info.get_branches(repo_root)
            try:
                if branch_exists:
                    attach_worktree(repo_root, branch=plan.branch, worktree_path=worktree_path)
                else:
                    if not plan.base_branch:
                        return PortfolioLaunchResult(
                            task_id=task.task_id, launched=False, message="не удалось создать worktree: base_branch не определён", plan=plan
                        )
                    create_worktree(repo_root, branch=plan.branch, worktree_path=worktree_path, base_branch=plan.base_branch)
                    branch_created = True
                worktree_created = True
            except PortfolioLaunchError as exc:
                return PortfolioLaunchResult(task_id=task.task_id, launched=False, message=str(exc), plan=plan)

        prompt = build_agent_prompt(task, plan)
        synthetic_task = _synthetic_task(task, plan, worktree_path=worktree_path, prompt=prompt)
        synthetic_tasks_by_id = {synthetic_task["id"]: synthetic_task}

        entries = execution_queue.load_queue(root)
        entries = execution_queue.enqueue(entries, synthetic_task, synthetic_tasks_by_id)
        new_entry = next(
            (e for e in entries if e.get("task_id") == task.task_id and e.get("state") in execution_queue.OPEN_STATES),
            None,
        )
        if new_entry is None:
            _rollback_worktree(
                repo_root, worktree_path, worktree_created=worktree_created, branch=plan.branch, branch_created=branch_created
            )
            return PortfolioLaunchResult(
                task_id=task.task_id, launched=False, message="не удалось создать запись очереди запуска", plan=plan
            )
        execution_queue.save_queue(root, entries)

        updated_entries, results = execution_queue.launch_ready(
            root,
            entries,
            [synthetic_task],
            synthetic_tasks_by_id,
            {task.project: {}},
            execution_center_api,
            entry_ids=[new_entry["id"]],
        )
        result = next((r for r in results if r.entry_id == new_entry["id"]), None)

        if result is None or not result.launched:
            execution_queue.save_queue(root, execution_queue.dequeue(updated_entries, new_entry["id"]))
            _rollback_worktree(
                repo_root, worktree_path, worktree_created=worktree_created, branch=plan.branch, branch_created=branch_created
            )
            message = result.message if result else "запуск не выполнен"
            return PortfolioLaunchResult(task_id=task.task_id, launched=False, message=message, plan=plan)

        registry[task.task_id] = {
            "task_id": task.task_id,
            "project": task.project,
            "branch": plan.branch,
            "worktree": str(worktree_path),
            "worktree_mode": plan.worktree_mode,
            "repository_root": str(repo_root),
            "base_branch": plan.base_branch,
            "base_sha": plan.base_sha,
            "run_id": result.run_id,
            "queue_entry_id": new_entry["id"],
            "launched_at": models.iso_now(),
            "source_path": str(task.source_path),
        }
        save_registry(root, registry)

        return PortfolioLaunchResult(task_id=task.task_id, launched=True, run_id=result.run_id, plan=plan)
    finally:
        _release(root, task.task_id)


def launch_batch(
    root: Path,
    tasks: list[PortfolioTask],
    *,
    tasks_by_id: dict[str, PortfolioTask],
    repository_paths: dict[str, str | None],
    execution_center_api,
    confirmed: bool,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_LAUNCHES,
) -> list[PortfolioLaunchResult]:
    """Launch every task in `tasks`, in order, reporting a result for every
    one. Two passes before any mutation:

    1. `_detect_batch_conflicts` — any two tasks in this same batch resolving
       to the same branch or worktree are both skipped with a blocking
       message (an override collision, since generated defaults are unique
       per task_id and can't collide with each other).
    2. The concurrency limit (`runtime.db.EXECUTION_CENTER_ACTIVE_STATES`
       count vs. `max_concurrent`) is checked before each remaining launch,
       reusing the Execution Center's own run-state accounting rather than a
       second concurrency tracker.
    """
    registry = load_registry(root)
    plans = build_batch_plan(tasks, tasks_by_id=tasks_by_id, repository_paths=repository_paths, registry=registry)
    batch_conflicts = _detect_batch_conflicts(plans)

    results: list[PortfolioLaunchResult] = []
    for task, plan in zip(tasks, plans):
        task_id = task.task_id or ""
        if task_id in batch_conflicts:
            results.append(PortfolioLaunchResult(task_id=task_id, launched=False, message=batch_conflicts[task_id], plan=plan))
            continue

        active_count = len(execution_center_api.list_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES))
        if active_count >= max_concurrent:
            results.append(
                PortfolioLaunchResult(
                    task_id=task_id,
                    launched=False,
                    message=f"достигнут лимит одновременных запусков ({max_concurrent})",
                )
            )
            continue
        results.append(
            launch_portfolio_task(
                root,
                task,
                tasks_by_id=tasks_by_id,
                repository_paths=repository_paths,
                execution_center_api=execution_center_api,
                confirmed=confirmed,
            )
        )
    return results
