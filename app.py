from __future__ import annotations

import html
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from command_center import (
    activity_log,
    agent_runner,
    artifacts,
    chat_service,
    execution_queue,
    executors,
    git_info,
    launch,
    launch_service,
    models,
    project_config,
    recommend,
    report_parser,
    storage,
    task_import,
    task_pipeline,
    task_view,
    tasks_repository,
    workflow,
    workspace_home,
    workspace_provisioning,
)
from command_center.runtime import api as runtime_api
from command_center.runtime import context_service as runtime_context_service
from command_center.runtime import db as runtime_db
from command_center.runtime import log_tail, project_overview, runs_read, scheduler, session_view, task_sync
from command_center.runtime import identity as runtime_identity
from command_center.runtime import supervisor as runtime_supervisor
from command_center.ui import (
    autopilot_panel,
    backlog_proposals,
    backlog_reconcile_panel,
    board_style,
    execution_strip,
    home_dashboard,
    live_board,
    waves_panel,
    content_area,
    portfolio_overview_panel,
    portfolio_panel,
    project_intelligence_panel,
    proposals_panel,
    project_selector,
    queue_panel,
    recommendations_panel,
    shell,
    tokens,
)

ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
GENERATED_DIR = ROOT / "generated"
REPORTS_DIR = ROOT / "reports"
CONTEXT_DIR = ROOT / "context"
DATA_DIR = storage.resolve_data_dir(ROOT)
TASKS_FILE = DATA_DIR / "tasks.json"
TASKS_EXAMPLE_FILE = DATA_DIR / "tasks.example.json"
START_TASK_SCRIPT = ROOT / "scripts" / "start-task.sh"

# The project registry is `models.PROJECT_IDS` — the single canonical list —
# never a second, hand-maintained dict here. A local `PROJECTS` dict used to
# live in this spot and silently omitted AICOS from every selector/filter
# that read it; see `docs/adr/` for the fix. `project_status_file_path`
# below is the only remaining project-id-keyed lookup this module needs,
# and it is backed by `project_config.PROJECT_STATUS_FILES` (itself keyed
# over every `models.PROJECT_IDS` entry), not a local dict.


def project_status_file_path(project_id: str) -> Path:
    relative = project_config.PROJECT_STATUS_FILES.get(project_id, f"projects/{project_id}.md")
    return ROOT / relative

CONTEXT_FILES: dict[str, str] = {
    "AIOS": "AIOS_CONTEXT.md",
    "BANK": "BANK_CONTEXT.md",
    "LEGAL": "LEGAL_CONTEXT.md",
}

# Canonical source: command_center.artifacts.TASK_TYPES — see that module's
# docstring for why app.py must not define its own duplicate list.
TASK_TYPES: tuple[str, ...] = artifacts.TASK_TYPES

TASK_TYPE_LABELS: dict[str, str] = {
    "implementation": "Реализация",
    "review": "Ревью",
    "remediation": "Исправление",
    "final_gate": "Финальная проверка",
    "architecture_review": "Архитектурный обзор",
}

AGENT_ROLES: dict[str, dict[str, object]] = {
    "implementation": {
        "title": "Инженер реализации",
        "summary": "Реализует поставленную цель под строгим контролем репозитория.",
        "rules": [
            "Изучить репозиторий перед изменением файлов.",
            "Реализовать только заявленную цель.",
            "Изменять только необходимые для задачи файлы.",
            "Добавить или обновить тесты для изменённого поведения.",
            "Не ослаблять существующие тесты.",
            "Не выполнять commit, push, merge, reset, stash, rebase.",
            "Запустить все применимые проверки.",
        ],
    },
    "review": {
        "title": "Независимый ревьюер",
        "summary": "Проводит read-only ревью без изменения файлов.",
        "rules": [
            "Не изменять ни один файл.",
            "Не выполнять commit, push, merge, reset, stash, rebase.",
            "Проверять фактическое состояние репозитория, а не прошлые заявления.",
            "Проверить поведение, тесты, контракты, безопасность и совместимость.",
            "Указывать находки с точными ссылками на файл и строку.",
            "Возвращать APPROVED только при отсутствии блокирующих проблем.",
        ],
    },
    "remediation": {
        "title": "Инженер по исправлениям",
        "summary": "Исправляет только независимо подтверждённые находки.",
        "rules": [
            "Исправлять только перечисленные находки.",
            "Не переделывать несвязанную архитектуру.",
            "Не изменять несвязанные файлы.",
            "Добавить регрессионные тесты для каждого исправления.",
            "Не ослаблять существующие тесты.",
            "Не выполнять commit, push, merge, reset, stash, rebase.",
            "Запустить все применимые проверки.",
        ],
    },
    "final_gate": {
        "title": "Финальный контролёр релиза",
        "summary": "Независимая финальная read-only проверка перед коммитом.",
        "rules": [
            "Не изменять ни один файл.",
            "Проверить полный diff и состояние рабочего дерева.",
            "Подтвердить, что все требуемые находки устранены.",
            "Подтвердить, что тесты реально покрывают исправленное поведение.",
            "Проверить упаковку, сгенерированные артефакты и документацию.",
            "Вернуть APPROVED FOR COMMIT или NOT APPROVED FOR COMMIT.",
        ],
    },
    "architecture_review": {
        "title": "Архитектурный ревьюер",
        "summary": "Независимый read-only обзор архитектуры.",
        "rules": [
            "Не изменять ни один файл.",
            "Оценить инварианты, владение, контракты, переходы состояний и отказоустойчивость.",
            "Проверить трассируемость между требованиями, архитектурой, рантаймом и тестами.",
            "Выявить неоднозначное или неавторитетное поведение.",
            "Указывать находки с уровнем серьёзности и точными ссылками.",
        ],
    },
}

# Canonical source: command_center.models.KANBAN_STATUSES / TASK_PRIORITIES —
# see that module's docstring for why app.py must not define its own
# duplicate lists (command_center.task_import validates against the same
# vocabulary and must never import app.py).
KANBAN_COLUMNS: list[str] = models.KANBAN_STATUSES

PRIORITIES: list[str] = models.TASK_PRIORITIES

# Canonical source: command_center.ui.tokens — see that module's docstring
# for why app.py must not define its own duplicate color dicts.
PRIORITY_COLORS: dict[str, str] = tokens.PRIORITY_COLORS
LAUNCH_STATUS_COLORS: dict[str, str] = tokens.LAUNCH_STATUS_COLORS

GLOBAL_FILES: list[str] = ["CURRENT_STATE.md", "DECISIONS.md", "INBOX.md"]

IGNORED_FILE_NAMES = {".DS_Store", ".gitkeep"}

NAV: dict[str, tuple[str, str]] = {
    "dashboard": ("Обзор", ":material/dashboard:"),
    "workspace_home": ("Workspace Home", ":material/home_work:"),
    "executive": ("Исполнительная панель", ":material/insights:"),
    "create": ("Создать задачу", ":material/add_task:"),
    "chat": ("Чат по проекту", ":material/forum:"),
    "kanban": ("Kanban", ":material/view_kanban:"),
    "waves": ("Волны", ":material/waves:"),
    "agents": ("AI-агенты", ":material/smart_toy:"),
    "execution_center": ("Live Execution Center", ":material/bolt:"),
    "runs": ("Журнал запусков", ":material/history:"),
    "timeline": ("Таймлайн", ":material/timeline:"),
    "projects": ("Проекты", ":material/folder_open:"),
    "generated": ("Сгенерированные задачи", ":material/description:"),
    "reports": ("Отчёты", ":material/summarize:"),
    "context": ("Глобальный контекст", ":material/menu_book:"),
    "git_center": ("Git Center", ":material/commit:"),
    "workspace": ("Workspace Launcher", ":material/rocket_launch:"),
    "focus": ("Focus Mode", ":material/center_focus_strong:"),
    "portfolio": ("Portfolio Execution", ":material/inventory_2:"),
    "portfolio_overview": ("Portfolio Overview", ":material/hub:"),
}


# --------------------------------------------------------------------------
# File and text helpers
# --------------------------------------------------------------------------


def read_text(path: Path) -> str:
    if not path.exists():
        return "Файл пока не создан."
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Ошибка чтения файла: {exc}"
    return content if content.strip() else "Файл пока пуст."


def format_mtime(path: Path) -> str:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        return "—"
    return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")


def format_estimate(hours: float) -> str:
    return f"{int(hours)}ч" if hours == int(hours) else f"{hours:g}ч"


# list_markdown_files / project_from_path / infer_task_type_from_filename now live in
# command_center/artifacts.py (Streamlit-free — see WORKSPACE_HOME_ARCHITECTURE.md
# §9/§9.1/§9.2). Imported at module top as `artifacts`; call sites below use
# `artifacts.list_markdown_files(...)` etc.


def gather_activity(limit: int = 20) -> list[tuple[Path, float]]:
    files: list[Path] = []
    for directory in (GENERATED_DIR, REPORTS_DIR, PROJECTS_DIR, CONTEXT_DIR):
        if directory.exists():
            files.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and path.name not in IGNORED_FILE_NAMES
            )
    for name in GLOBAL_FILES:
        candidate = ROOT / name
        if candidate.exists():
            files.append(candidate)

    dated = [(path, path.stat().st_mtime) for path in files]
    dated.sort(key=lambda item: item[1], reverse=True)
    return dated[:limit]


def parse_project_statuses() -> dict[str, str]:
    """Best-effort extraction of 'Status: X' lines per project section in CURRENT_STATE.md."""
    content = read_text(ROOT / "CURRENT_STATE.md")
    statuses: dict[str, str] = {}
    current_project: str | None = None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].lower()
            current_project = next(
                (key for key in models.PROJECT_IDS if key.lower() in heading), None
            )
        elif stripped.lower().startswith("status:") and current_project and current_project not in statuses:
            statuses[current_project] = stripped.split(":", 1)[1].strip()

    return statuses


# --------------------------------------------------------------------------
# Task persistence (data/tasks.json)
# --------------------------------------------------------------------------


# Task persistence itself lives in `command_center.tasks_repository` (pure
# Python, no Streamlit) — see `docs/adr/0001-engineering-control-center-v2-
# increment-1.md`. These are thin wrappers binding in this app's ROOT/
# TASKS_EXAMPLE_FILE constants so every existing call site below is unchanged.


def normalize_task(task: dict) -> dict:
    return tasks_repository.normalize_task(task)


def load_tasks() -> list[dict]:
    return tasks_repository.load_tasks(ROOT, example_file=TASKS_EXAMPLE_FILE)


# No `save_tasks(tasks)` wrapper here (deliberately removed): writing back
# whatever `tasks` this script run loaded at the top would silently discard
# any concurrent writer's change made since that load (see
# `tasks_repository`'s module docstring). Every write path in this file goes
# through `create_task`/`update_task_status`/`delete_task`/`upsert_tasks`/
# `tasks_repository.upsert_task`/`tasks_repository.mutate_tasks` instead,
# each of which locks and reloads fresh immediately before writing.


def upsert_tasks(tasks: list[dict]) -> None:
    """The `save_tasks_fn` callback handed to `recommendations_panel`/
    `queue_panel`: both mutate a subset of `tasks_by_id`'s dicts in place
    (via `execution_queue.launch_ready`, exactly like `launch_service`) and
    need to commit exactly those changes. Locked bulk upsert, not a blind
    overwrite of this script run's entire (possibly-stale) `tasks` snapshot —
    see `tasks_repository.upsert_tasks`."""
    tasks_repository.upsert_tasks(ROOT, tasks)


def new_task_record(
    project: str,
    title: str,
    task_type: str,
    status: str,
    *,
    goal: str | None = None,
    notes: str = "",
    priority: str = "Medium",
    owner: str = "",
    estimate_hours: float = 0.0,
    depends_on: list[str] | None = None,
    parent_task_id: str | None = None,
    prior_run_id: str | None = None,
    workflow_stage: str = "Draft",
    workspace_path: str | None = None,
    branch: str | None = None,
    executor: str | None = None,
    prompt: str | None = None,
) -> dict:
    return tasks_repository.new_task_record(
        project,
        title,
        task_type,
        status,
        goal=goal,
        notes=notes,
        priority=priority,
        owner=owner,
        estimate_hours=estimate_hours,
        depends_on=depends_on,
        parent_task_id=parent_task_id,
        prior_run_id=prior_run_id,
        workflow_stage=workflow_stage,
        workspace_path=workspace_path,
        branch=branch,
        executor=executor,
        prompt=prompt,
    )


def create_task(
    project: str,
    title: str,
    task_type: str,
    status: str,
    *,
    goal: str | None = None,
    notes: str = "",
    priority: str = "Medium",
    owner: str = "",
    estimate_hours: float = 0.0,
    depends_on: list[str] | None = None,
    parent_task_id: str | None = None,
    prior_run_id: str | None = None,
    workflow_stage: str = "Draft",
    workspace_path: str | None = None,
    branch: str | None = None,
    executor: str | None = None,
    prompt: str | None = None,
) -> dict:
    """Locked create — every page that adds a task to the Kanban board must
    call this (never `tasks.append(new_task_record(...)); save_tasks(tasks)`
    against its own possibly-stale in-memory `tasks` list, which is exactly
    the pattern that silently drops a concurrent writer's task). See
    `tasks_repository.create_task`/module docstring."""
    return tasks_repository.create_task(
        ROOT,
        project,
        title,
        task_type,
        status,
        goal=goal,
        notes=notes,
        priority=priority,
        owner=owner,
        estimate_hours=estimate_hours,
        depends_on=depends_on,
        parent_task_id=parent_task_id,
        prior_run_id=prior_run_id,
        workflow_stage=workflow_stage,
        workspace_path=workspace_path,
        branch=branch,
        executor=executor,
        prompt=prompt,
    )


def update_task_status(task_id: str, new_status: str) -> dict | None:
    return tasks_repository.update_task_status(ROOT, task_id, new_status)


def delete_task(task_id: str) -> None:
    tasks_repository.delete_task(ROOT, task_id)


def task_label(task: dict) -> str:
    return tasks_repository.task_label(task)


def unmet_dependencies(task: dict, tasks_by_id: dict[str, dict]) -> list[str]:
    return models.unmet_dependencies(task, tasks_by_id)


def is_blocked(task: dict, tasks_by_id: dict[str, dict]) -> bool:
    return models.is_blocked(task, tasks_by_id)


# --------------------------------------------------------------------------
# Task generation (scripts/start-task.sh)
# --------------------------------------------------------------------------


def run_start_task_script(
    project: str,
    task_type: str,
    objective: str,
    timeout: int = 30,
) -> tuple[bool, str, str]:
    if not START_TASK_SCRIPT.exists():
        return False, "", f"Скрипт не найден: {START_TASK_SCRIPT}"
    if not os.access(START_TASK_SCRIPT, os.X_OK):
        return False, "", f"Скрипт не является исполняемым: {START_TASK_SCRIPT}"

    try:
        result = subprocess.run(
            [str(START_TASK_SCRIPT), project, task_type, objective],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"Превышено время ожидания выполнения скрипта ({timeout} сек)."
    except OSError as exc:
        return False, "", f"Не удалось запустить скрипт: {exc}"

    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


# --------------------------------------------------------------------------
# Git (read-only) — thin wrappers over command_center.git_info, pinned to ROOT
# --------------------------------------------------------------------------


def run_git_command(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess | None:
    return git_info.run_git_command(ROOT, args, timeout=timeout)


def get_git_status() -> dict[str, object]:
    return git_info.get_status(ROOT)


def get_git_log(limit: int = 20) -> list[dict[str, str]]:
    return git_info.get_log(ROOT, limit=limit)


def get_git_diff_stat(staged: bool = False) -> str:
    return git_info.get_diff_stat(ROOT, staged=staged)


def get_git_branches() -> list[str]:
    return git_info.get_branches(ROOT)


def get_git_remotes() -> list[tuple[str, str]]:
    return git_info.get_remotes(ROOT)


def get_git_worktrees() -> list[dict[str, str]]:
    return git_info.get_worktrees(ROOT)


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def build_timeline_events(
    tasks: list[dict],
    runs: list[dict] | None = None,
    activity_events: list[dict] | None = None,
    limit: int = 200,
) -> list[dict]:
    events: list[dict] = []

    for task in tasks:
        created = task.get("created_at")
        created_ts = _parse_iso_ts(created)
        if created_ts is not None:
            events.append(
                {
                    "ts": created_ts,
                    "icon": ":material/add_task:",
                    "label": f"Задача создана: {(task.get('title') or '')[:80]}",
                    "project": task.get("project"),
                }
            )

        updated = task.get("updated_at")
        if updated and updated != created:
            updated_ts = _parse_iso_ts(updated)
            if updated_ts is not None:
                events.append(
                    {
                        "ts": updated_ts,
                        "icon": ":material/sync_alt:",
                        "label": f"Статус «{task.get('status')}»: {(task.get('title') or '')[:80]}",
                        "project": task.get("project"),
                    }
                )

    for run in runs or []:
        run_ts = _parse_iso_ts(run.get("created_at"))
        if run_ts is not None:
            verdict = (run.get("parsed") or {}).get("verdict")
            suffix = f" · {verdict}" if verdict else ""
            events.append(
                {
                    "ts": run_ts,
                    "icon": ":material/smart_toy:",
                    "label": f"Запуск {run.get('task_type')} ({models.RUN_STATUS_LABELS.get(run.get('status'), run.get('status'))}){suffix}",
                    "project": run.get("project"),
                }
            )

    for event in activity_events or []:
        event_ts = _parse_iso_ts(event.get("ts"))
        if event_ts is not None:
            events.append(
                {
                    "ts": event_ts,
                    "icon": ":material/bolt:",
                    "label": event.get("message") or event.get("type") or "",
                    "project": event.get("project"),
                }
            )

    for path, mtime in gather_activity(limit=limit):
        project = None
        for base in (GENERATED_DIR, REPORTS_DIR):
            if base in path.parents:
                candidate = artifacts.project_from_path(path, base)
                project = candidate if candidate != "—" else None
                break
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path
        events.append(
            {
                "ts": mtime,
                "icon": ":material/description:",
                "label": f"Файл: {rel}",
                "project": project,
            }
        )

    events.sort(key=lambda event: event["ts"], reverse=True)
    return events[:limit]


# --------------------------------------------------------------------------
# Agent workflow (v1.2): launcher, next-task suggestion, project chat helpers
# --------------------------------------------------------------------------


def _build_project_context_text(project: str) -> str:
    cfg = project_config.get_project_config(project)
    parts: list[str] = []
    for rel_path in cfg.get("context_file_paths", []):
        path = ROOT / rel_path
        if path.exists():
            parts.append(f"### {rel_path}\n\n{read_text(path)}")
    return "\n\n".join(parts)


def _save_message_as_report(conversation: dict, message: dict) -> Path:
    project = conversation["project"]
    report_dir = REPORTS_DIR / project
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}_chat_{conversation['id'][:8]}.md"
    path = report_dir / filename
    content = (
        "# Сообщение чата, сохранённое как отчёт\n\n"
        f"Project: {project}\n"
        f"Conversation: {conversation['id']}\n"
        f"Role: {message['role']}\n"
        f"Provider: {message.get('provider') or '—'}\n"
        f"Timestamp: {message.get('created_at')}\n\n"
        "---\n\n"
        f"{message['content']}\n"
    )
    path.write_text(content, encoding="utf-8")
    activity_log.log_event(
        "report_saved", project=project, conversation_id=conversation["id"], message=filename
    )
    return path


def render_agent_launcher(
    *,
    key_prefix: str,
    project: str,
    default_prompt: str,
    tasks: list[dict],
    task_id: str | None = None,
    default_task_type: str = "implementation",
) -> None:
    """Confirm-then-execute Claude Code launcher, reused from every required entry
    point (task detail card, Project Chat, AI Agents, generated-task preview).

    The workspace to validate and launch against is resolved exactly once, via
    `launch.resolve_workspace_path` (task workspace_path → project
    default_workspace_path → project repository_path), and that same path is
    then reused for every later step — validation, git reads, the Terminal/
    Folder actions, the actual Claude launch, and persisted launch history —
    instead of being recomputed (and risking drifting) at each step."""
    cfg = project_config.get_project_config(project)
    repo_path = cfg.get("repository_path")
    confirm_key = f"{key_prefix}_confirm_open"
    st.session_state.setdefault(confirm_key, False)

    if st.button("Запустить агента", key=f"{key_prefix}_open_btn", icon=":material/smart_toy:"):
        st.session_state[confirm_key] = True

    if not st.session_state[confirm_key]:
        return

    # Rendered as a dialog (not inline `with st.container(...)`) so this
    # multi-column confirmation form always gets a full-width modal surface
    # regardless of how narrow the caller's own layout is — e.g. a single
    # Kanban lane (`st.columns(len(KANBAN_COLUMNS))`), which used to force
    # this entire form into a sliver a few hundred pixels wide.
    @st.dialog("Подтверждение запуска агента", width="large")
    def _render_launch_confirmation() -> None:
        task_for_launch = next((t for t in tasks if t.get("id") == task_id), None) if task_id else None
        selection = launch.resolve_workspace_path(task=task_for_launch, project_config=cfg)

        if not selection.path:
            st.error(
                f"Не удалось определить workspace для запуска: не заданы ни workspace задачи, "
                f"ни workspace проекта по умолчанию, ни путь к репозиторию для проекта {project} — "
                "ничего не настроено. Настройте путь к репозиторию в разделе «Проекты» → "
                "«Настройки репозитория» или укажите workspace_path у задачи."
            )
            if st.button("Закрыть", key=f"{key_prefix}_cancel_noconfig"):
                st.session_state[confirm_key] = False
                st.rerun()
            return

        if cfg.get("sensitive"):
            st.warning(
                f"Проект {project} — чувствительный (BANK/LEGAL). Файлы не прикрепляются "
                "автоматически: добавьте разрешённый контекст вручную ниже, если он нужен."
            )

        default_type = default_task_type if default_task_type in TASK_TYPES else TASK_TYPES[0]
        task_type = st.selectbox(
            "Тип задачи",
            TASK_TYPES,
            index=TASK_TYPES.index(default_type),
            format_func=lambda value: TASK_TYPE_LABELS.get(value, value),
            key=f"{key_prefix}_task_type",
        )
        try:
            executor_options = list(project_config.allowed_execution_providers(project))
        except project_config.ProviderAuthorizationError as exc:
            st.error(str(exc))
            return
        configured_executor = (
            (task_for_launch or {}).get("executor") or cfg.get("default_executor") or "claude_code"
        )
        if configured_executor not in executor_options:
            configured_executor = executor_options[0]
        executor_id = st.selectbox(
            "Execution provider",
            executor_options,
            index=executor_options.index(configured_executor),
            format_func=lambda value: executors.get_executor(value).label,
            key=f"{key_prefix}_executor",
        )
        selected_executor = executors.get_executor(executor_id)
        provider_availability = selected_executor.availability
        if provider_availability is not None:
            availability_text = (
                f"Доступен: {provider_availability.version or provider_availability.executable or 'да'}"
                if provider_availability.available
                else f"Недоступен ({provider_availability.code}): {provider_availability.message}"
            )
            (st.caption if provider_availability.available else st.error)(availability_text)
        prompt = st.text_area(
            "Промпт для агента", value=default_prompt, height=220, key=f"{key_prefix}_prompt"
        )
        extra_context = ""
        if cfg.get("sensitive"):
            extra_context = st.text_area(
                "Дополнительный контекст (вставьте вручную при необходимости)",
                key=f"{key_prefix}_extra_context",
                height=120,
            )
        timeout_seconds = st.number_input(
            "Таймаут (секунды)",
            min_value=agent_runner.MIN_TIMEOUT_SECONDS,
            max_value=agent_runner.MAX_TIMEOUT_SECONDS,
            value=agent_runner.DEFAULT_TIMEOUT_SECONDS,
            step=30,
            key=f"{key_prefix}_timeout",
        )

        full_prompt = prompt
        if extra_context.strip():
            full_prompt = f"{prompt}\n\n## Дополнительный контекст (предоставлен пользователем)\n\n{extra_context.strip()}"

        # Shared, non-mutating classification (same service the execution
        # queue uses). Distinguishes a fatal validation error from a
        # missing-but-provisionable workspace so the launch is not permanently
        # blocked just because the isolated worktree does not exist yet — it
        # will be created (and fail-closed verified) downstream on click.
        prep = launch_service.prepare_task_launch(task=task_for_launch, project_config=cfg)
        expected_branch = prep.expected_branch
        base_branch = prep.base_branch
        validation = prep.validation
        actual_branch = (validation.git_status or {}).get("branch") if validation.git_status else None

        st.markdown("**Проверьте перед запуском:**")
        st.write(f"- Проект: `{project}`")
        st.write(f"- Репозиторий проекта: `{repo_path or '—'}`")
        st.write(f"- Выбранный workspace: `{selection.path}`")
        st.write(f"- Источник workspace: {launch.WORKSPACE_SOURCE_LABELS.get(selection.source, selection.source)}")
        st.write(f"- Ожидаемая ветка: `{expected_branch or '—'}`")
        st.write(f"- Текущая ветка: `{actual_branch or '—'}`")
        st.write(f"- Агент: `{executor_id}` ({selected_executor.label})")
        st.write(f"- Тип задачи: `{task_type}`")

        if prep.provisionable:
            # Recoverable, not fatal: don't render the "not found" error, tell
            # the operator the isolated worktree will be created automatically.
            st.info(prep.provision_notice or "Workspace будет создан автоматически как изолированный worktree.")
        else:
            for error in validation.errors:
                st.error(error)
        for warning in validation.warnings:
            st.warning(warning)

        st.markdown("**Workspace-действия (не запускают агента):**")
        workspace_action_cols = st.columns(3)
        with workspace_action_cols[0]:
            if st.button("Открыть Workspace", key=f"{key_prefix}_open_folder"):
                action_ok, action_message = launch.open_folder_at(selection.path)
                (st.success if action_ok else st.error)(action_message)
        with workspace_action_cols[1]:
            if st.button("Открыть терминал", key=f"{key_prefix}_open_terminal"):
                action_ok, action_message = launch.open_terminal_at(selection.path)
                (st.success if action_ok else st.error)(action_message)
        with workspace_action_cols[2]:
            if st.button("Копировать промпт", key=f"{key_prefix}_copy_prompt"):
                action_ok, action_message = launch.copy_to_clipboard(full_prompt)
                (st.success if action_ok else st.error)(action_message)

        confirmed = st.checkbox(
            "Я подтверждаю запуск внешнего агента с указанными параметрами.",
            key=f"{key_prefix}_confirmed",
        )
        warnings_ack = True
        if validation.warnings:
            warnings_ack = st.checkbox(
                "Я подтверждаю запуск несмотря на предупреждения выше.",
                key=f"{key_prefix}_warnings_ack",
            )
        action_cols = st.columns(2)
        with action_cols[0]:
            launch_clicked = st.button(
                "Подтвердить и запустить",
                type="primary",
                key=f"{key_prefix}_launch_btn",
                disabled=(
                    not confirmed
                    # `prep.launchable` supersedes the raw `validation.can_launch`:
                    # a missing-but-provisionable workspace is launchable.
                    or not prep.launchable
                    or not warnings_ack
                    or not bool(provider_availability and provider_availability.available)
                ),
                icon=":material/play_arrow:",
            )
        with action_cols[1]:
            if st.button("Отмена", key=f"{key_prefix}_cancel_btn"):
                st.session_state[confirm_key] = False
                st.rerun()

        if not launch_clicked:
            return

        # Defense in depth: `disabled=` on the button above is the primary
        # gate, but a launch this consequential should not depend solely on
        # a widget attribute — re-check server-side before doing anything.
        # `prep.launchable` admits both an already-valid workspace and a
        # missing-but-provisionable one; a fatal validation error is still
        # refused here, and the fail-closed isolation gate downstream
        # (`provision_and_verify` -> `Supervisor.start_raw`) is unchanged.
        if not prep.launchable:
            st.error("Запуск заблокирован ошибками валидации выше — сначала устраните их.")
            return
        if validation.warnings and not warnings_ack:
            st.error("Подтвердите предупреждения выше перед запуском.")
            return

        # `selection.path` was already validated above (existence, is_dir,
        # is a git repo) — resolved the same way `agent_runner.
        # validate_repository` used to (`expanduser().resolve()`), so
        # symlink/`..` tricks can't escape it, but against the *selected*
        # workspace rather than always forcing the project's repository_path.
        resolved_workspace = Path(selection.path).expanduser().resolve()

        # Real, PID-tracked, cancellable v2 run — not a blocking call. The
        # button click above already re-validated `confirmed`/`warnings_ack`
        # server-side, so `confirmed=True` here reflects a genuine, already-
        # checked confirmation, not a bypass of it.
        try:
            run = launch_service.execute_agent_launch_v2(
                project=project,
                task_type=task_type,
                prompt=full_prompt,
                timeout_seconds=int(timeout_seconds),
                repository_path=resolved_workspace,
                execution_center_api=get_execution_center_api(),
                confirmed=True,
                task=task_for_launch,
                executor_id=executor_id,
                validation=validation,
                expected_branch=expected_branch,
                base_branch=base_branch,
                source_repository_path=repo_path,
                on_task_state_changed=(
                    (lambda: tasks_repository.upsert_task(ROOT, task_for_launch))
                    if task_for_launch is not None
                    else None
                ),
            )
        except (
            workspace_provisioning.WorkspaceVerificationError,
            runtime_supervisor.WorkspaceVerificationFailed,
        ) as exc:
            # Fail closed: the workspace did not verify (wrong branch, not an
            # isolated worktree, belongs to another repo, ...). The agent was
            # never started; show the structured failure so the operator can
            # fix it, and never fall back to the main repository.
            structured = exc.structured if isinstance(exc, runtime_supervisor.WorkspaceVerificationFailed) else exc.as_dict()
            st.error("Запуск заблокирован: workspace не прошёл обязательную проверку изоляции.")
            st.markdown(
                f"- Проваленная проверка: `{structured['failed_step']}`\n"
                f"- Ожидаемый workspace: `{structured['expected_workspace']}`\n"
                f"- Фактический workspace: `{structured.get('actual_workspace') or '—'}`\n"
                f"- Ожидаемая ветка: `{structured.get('expected_branch') or '—'}`\n"
                f"- Фактическая ветка: `{structured.get('actual_branch') or '—'}`\n"
                f"- Причина: {structured.get('detail') or '—'}\n"
                f"- Рекомендация: {structured.get('remediation') or '—'}"
            )
            return
        except (
            runtime_context_service.ConfirmationRequiredError,
            agent_runner.RunnerError,
            project_config.ProviderAuthorizationError,
            runtime_supervisor.SupervisorError,
            launch_service.DuplicateActiveLaunchError,
        ) as exc:
            st.error(str(exc))
            return

        if task_for_launch is not None:
            # `task_for_launch` was already committed via `on_task_state_changed`
            # above at each of its in-place mutation checkpoints — this final
            # upsert is a defense-in-depth flush in case any *future* code
            # between here and the last checkpoint mutates it again. Never
            # `save_tasks(tasks)`: `tasks` is this script run's own snapshot,
            # loaded once at the top — persisting it verbatim would silently
            # discard whatever a concurrent writer (another tab, an import,
            # ...) committed to `tasks.json` in the meantime.
            tasks_repository.upsert_task(ROOT, task_for_launch)

        st.session_state[confirm_key] = False
        st.success(
            f"Запуск начат: `{run['id']}`. Отслеживайте прогресс, workspace, ветку и логи "
            "в разделе «Live Execution Center»."
        )
        st.session_state.pending_nav = "execution_center"
        st.session_state.pending_exec_center_run = run["id"]
        st.rerun()

    _render_launch_confirmation()


def render_create_next_task_widget(run: dict, tasks: list[dict], key_prefix: str) -> None:
    if run.get("status") != "completed":
        st.caption("Кнопка «Создать следующую задачу» доступна только для завершённых запусков.")
        return

    suggestion = workflow.suggest_next_task(run)
    with st.expander("Создать следующую задачу", icon=":material/add_task:"):
        st.caption(
            f"Источник: вердикт «{suggestion['source_verdict'] or 'не определён'}» "
            f"из запуска `{run['id'][:8]}`."
        )
        if suggestion.get("contradictory"):
            st.warning(
                "Отчёт содержит противоречивые вердикты (найдено более одного). "
                "Показан наиболее консервативный вариант — проверьте отчёт вручную, "
                "при необходимости внесите ручную корректировку вердикта на странице "
                "«Журнал запусков» перед созданием задачи."
            )
        project = run.get("project")
        choices = suggestion["task_type_choices"] or TASK_TYPES
        default_type = suggestion["task_type"] or choices[0]
        next_task_type = st.selectbox(
            "Тип следующей задачи",
            choices,
            index=choices.index(default_type) if default_type in choices else 0,
            format_func=lambda value: TASK_TYPE_LABELS.get(value, value),
            key=f"{key_prefix}_next_type",
        )
        objective = st.text_area(
            "Цель следующей задачи (черновик — проверьте перед созданием)",
            value=suggestion["objective_draft"],
            height=220,
            key=f"{key_prefix}_next_objective",
        )
        next_stage = st.selectbox(
            "Стадия workflow",
            models.WORKFLOW_STAGES,
            index=(
                models.WORKFLOW_STAGES.index(suggestion["workflow_stage"])
                if suggestion["workflow_stage"] in models.WORKFLOW_STAGES
                else 0
            ),
            format_func=lambda value: models.WORKFLOW_STAGE_LABELS.get(value, value),
            key=f"{key_prefix}_next_stage",
        )

        if st.button("Создать задачу", key=f"{key_prefix}_create_next_btn", type="primary", icon=":material/add_task:"):
            objective_clean = objective.strip()
            if not objective_clean:
                st.error("Укажите цель задачи.")
                return
            new_task = create_task(
                project,
                models.derive_short_title(objective_clean),
                next_task_type,
                "Backlog",
                goal=objective_clean,
                parent_task_id=run.get("task_id"),
                prior_run_id=run["id"],
                workflow_stage=next_stage,
            )
            run["next_task_id"] = new_task["id"]
            agent_runner.append_run(run)
            activity_log.log_event(
                "next_task_created", project=project, task_id=new_task["id"], run_id=run["id"],
                message=f"Создана задача из запуска {run['id'][:8]}",
            )
            st.success(f"Задача создана: {new_task['title'][:60]}")
            st.rerun()


# --------------------------------------------------------------------------
# Task Card — shared component (Title/Progress/Stage/Project/Executor/
# Repository/Workspace/Branch/Git/PR/Tests + action row), used by kanban
# and focus mode so every task-summary view stays visually/behaviorally
# consistent instead of each page duplicating its own inline markup.
# --------------------------------------------------------------------------


# Pure task-card read-model logic lives in `command_center.task_view` — see
# `docs/adr/0001-engineering-control-center-v2-increment-1.md`. These
# render_* functions only turn its plain-data output into widgets.


def _set_launch_status(task_id: str, status: str, note: str) -> None:
    tasks_repository.set_manual_launch_status(ROOT, task_id, status, note)


def render_task_timeline(task: dict) -> None:
    events = task_view.sorted_timeline(task)
    if not events:
        st.caption("История ещё пуста.")
        return
    for event in events:
        st.caption(f"`{event.get('ts', '—')}` · **{event.get('type', '—')}** — {event.get('message', '')}")


def render_dependency_graph(task: dict, tasks_by_id: dict[str, dict]) -> None:
    dot = task_view.dependency_graph_dot(task, tasks_by_id)
    if dot is None:
        st.caption("Нет связанных задач.")
        return
    st.graphviz_chart(dot)


def render_task_card(
    task: dict,
    *,
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    key_prefix: str,
    git_status_cache: dict[str, dict],
    show_kanban_controls: bool = False,
) -> None:
    task_id = task.get("id")
    title = task.get("title") or "Без названия"
    workspace_path = task.get("workspace_path") or task.get("repository_path")

    with st.container(border=True):
        st.markdown(f"### {title}")
        st.caption(f"{task.get('project')} · {TASK_TYPE_LABELS.get(task.get('task_type'), task.get('task_type'))}")

        progress = int(task.get("progress") or 0)
        stage = task.get("current_stage") or models.EXECUTION_STAGES[0]
        st.progress(progress / 100, text=f"{stage} — {progress}%")

        # Three distinct, visually separated clusters — planning state
        # (this Kanban lane, owned by the user), current execution state
        # (`launch_status`, synced live from `runtime.db` per ADR 0003 —
        # never a manual Kanban lane), and dependency readiness (derived,
        # never stored) — deliberately never merged into one badge row, so
        # "what lane is this in," "is an agent actually running against it
        # right now," and "is it blocked on something else" each read as
        # their own answer instead of one ambiguous chip soup.
        with st.container(horizontal=True):
            priority = task.get("priority", "Medium")
            st.badge(priority, color=PRIORITY_COLORS.get(priority, "blue"))
            if task.get("owner"):
                st.badge(task["owner"], color="gray", icon=":material/person:")
            if task.get("estimate_hours"):
                st.badge(format_estimate(task["estimate_hours"]), color="gray", icon=":material/schedule:")

        with st.container(horizontal=True):
            launch_status = task.get("launch_status", "Ready")
            running = launch_status == "Running"
            st.badge(
                f"⏺ {launch_status}" if running else launch_status,
                color=LAUNCH_STATUS_COLORS.get(launch_status, "gray"),
            )
            executor_id = task.get("executor")
            if executor_id:
                st.badge(executors.get_executor(executor_id).label, color="blue", icon=":material/smart_toy:")
            if task.get("branch"):
                st.badge(task["branch"], color="gray", icon=":material/fork_right:")
            if task.get("current_run_id"):
                st.caption(f"run `{task['current_run_id'][:8]}` · Live Execution Center")

        blocked = models.is_blocked(task, tasks_by_id)
        if blocked:
            with st.container(horizontal=True):
                st.badge("Заблокировано", color="red", icon=":material/block:")
                unmet_names = ", ".join(
                    (tasks_by_id.get(dep_id, {}).get("title") or dep_id)
                    for dep_id in models.unmet_dependencies(task, tasks_by_id)
                )
                st.caption(f"Ожидает: {unmet_names}")
        elif task.get("depends_on"):
            st.badge("Зависимости выполнены", color="green", icon=":material/check_circle:")

        git_status = task_view.cached_git_status(workspace_path, git_status_cache)
        with st.container(horizontal=True):
            if git_status.get("is_repo"):
                dirty = git_status.get("dirty")
                st.badge("Изменения" if dirty else "Чисто", color="orange" if dirty else "green", icon=":material/commit:")
            if task.get("pull_request_url"):
                st.link_button("PR", task["pull_request_url"], icon=":material/merge:")
            if task.get("latest_verdict"):
                passing = models.is_passing_verdict(task["latest_verdict"])
                st.badge(
                    models.VERDICT_LABELS.get(task["latest_verdict"], task["latest_verdict"]),
                    color="green" if passing else "red",
                )

        with st.expander("Действия", icon=":material/tune:"):
            st.caption(f"ID: `{task_id}` · Создано: {task.get('created_at', '—')} · Обновлено: {task.get('updated_at', '—')}")
            st.caption(
                f"Стадия workflow: "
                f"{models.WORKFLOW_STAGE_LABELS.get(task.get('workflow_stage'), task.get('workflow_stage') or '—')}"
            )
            if task.get("goal"):
                st.caption(f"Цель: {task['goal']}")
            if task.get("notes"):
                st.caption(f"Заметки: {task['notes']}")
            st.caption(f"Репозиторий: `{task.get('repository_path') or '—'}` · Workspace: `{workspace_path or '—'}`")

            action_cols = st.columns(5)
            with action_cols[0]:
                if st.button("Workspace", key=f"{key_prefix}_action_workspace", icon=":material/folder_open:"):
                    if workspace_path:
                        ok_action, message_action = launch.open_folder_at(workspace_path)
                        (st.success if ok_action else st.error)(message_action)
                    else:
                        st.error("Workspace не настроен.")
            with action_cols[1]:
                git_open = st.button("Git", key=f"{key_prefix}_action_git", icon=":material/commit:")
            with action_cols[2]:
                if st.button("Промпт", key=f"{key_prefix}_action_prompt", icon=":material/content_copy:"):
                    prompt_text = task.get("prompt") or task.get("goal") or ""
                    ok_action, message_action = launch.copy_to_clipboard(prompt_text)
                    (st.success if ok_action else st.error)(message_action)
            with action_cols[3]:
                report_open = st.button("Отчёт", key=f"{key_prefix}_action_report", icon=":material/description:")
            with action_cols[4]:
                if st.button("В очередь", key=f"{key_prefix}_action_queue", icon=":material/playlist_add:"):
                    # Lost-update-safe: the whole load→enqueue→save cycle runs
                    # under `queue_lock` so a concurrent writer's queue change is
                    # never clobbered by a stale snapshot (see execution_queue).
                    execution_queue.enqueue_and_persist(ROOT, task, tasks_by_id)
                    st.success("Добавлено в очередь запуска.")

            if git_open:
                if workspace_path and git_status.get("is_repo"):
                    st.write(f"Ветка: `{git_status.get('branch')}`")
                    st.write(f"Последний коммит: `{git_status.get('last_commit_hash')}` — {git_status.get('last_commit_subject')}")
                    st.write(f"Изменено файлов: {git_status.get('modified_count', 0)}, неотслеживаемых: {git_status.get('untracked_count', 0)}")
                else:
                    st.warning("Workspace не является git-репозиторием или не настроен.")

            if report_open:
                if task.get("report_path"):
                    report_full_path = ROOT / task["report_path"]
                    st.code(read_text(report_full_path), language="markdown")
                else:
                    st.caption("Отчёт ещё не создан.")

            # "Ручной статус" — honest framing (UX analysis §3.5): these are
            # planning labels, NOT process control. Grouped under a caption that
            # says so, and localized, so the row no longer reads as a media-style
            # transport that can pause a running agent (it cannot). Real
            # cancellation lives only on the run card in the Execution Center.
            st.markdown("**Ручной статус** (метка плана, не управление процессом)")
            status_cols = st.columns(3)
            with status_cols[0]:
                if st.button("Приостановить", key=f"{key_prefix}_action_pause", icon=":material/pause:"):
                    _set_launch_status(task_id, "Requires Attention", "Отмечено как приостановлено (вручную).")
                    st.rerun()
            with status_cols[1]:
                if st.button("Возобновить", key=f"{key_prefix}_action_resume", icon=":material/play_arrow:"):
                    _set_launch_status(task_id, "Ready", "Отмечено как возобновлено (вручную).")
                    st.rerun()
            with status_cols[2]:
                if st.button("К перезапуску", key=f"{key_prefix}_action_restart", icon=":material/restart_alt:"):
                    _set_launch_status(task_id, "Ready", "Отмечено для перезапуска (вручную).")
                    st.rerun()
            st.caption(
                "Это статус-метки для планирования, а не управление процессом: "
                "синхронный запуск Claude Code нельзя приостановить на лету. "
                "Реальная отмена прогона — на карточке в «Live Execution Center»."
            )

            st.divider()
            st.markdown("**Запуск**")
            render_agent_launcher(
                key_prefix=f"{key_prefix}_launch",
                project=task.get("project"),
                default_prompt=task.get("prompt") or task.get("goal") or title,
                tasks=tasks,
                task_id=task_id,
                default_task_type=task.get("task_type", "implementation"),
            )

            st.divider()
            st.markdown("**История**")
            render_task_timeline(task)

            st.markdown("**Зависимости**")
            deps = task.get("depends_on") or []
            if deps:
                for dep_id in deps:
                    dep = tasks_by_id.get(dep_id)
                    label = f"{(dep.get('title') or '')[:50]} ({dep.get('status')})" if dep else f"(удалена) {dep_id}"
                    st.caption(f"- {label}")
            render_dependency_graph(task, tasks_by_id)

        if show_kanban_controls:
            current_status = task.get("status", KANBAN_COLUMNS[0])
            new_status = st.selectbox(
                "Статус",
                KANBAN_COLUMNS,
                index=KANBAN_COLUMNS.index(current_status) if current_status in KANBAN_COLUMNS else 0,
                key=f"{key_prefix}_status_select",
                label_visibility="collapsed",
            )
            if new_status != current_status:
                update_task_status(task_id, new_status)
                st.rerun()

            if st.button("Удалить", key=f"{key_prefix}_delete", icon=":material/delete:", width="stretch"):
                delete_task(task_id)
                st.rerun()


def render_next_task_callout(tasks: list[dict], project: str | None = None, *, active_runs: list[dict] | None = None) -> None:
    """Non-invasive '➡ Next Task' recommendation, always with an explanation
    of *why* — see `command_center.recommend.recommend_next_task`. Never
    creates, launches, or modifies anything; purely advisory.

    ``active_runs`` (runtime.db runs filtered to active states) makes the
    "исполнитель занят" reason agree with the Launch Gate instead of the
    lagging Kanban ``launch_status``; see ``recommend._score_candidates``."""
    recommendation = recommend.recommend_next_task(tasks, project=project, active_runs=active_runs)
    if recommendation is None:
        st.info("➡ Следующая задача: нет открытых незаблокированных задач.")
        return

    task = recommendation.task
    with st.container(border=True):
        st.markdown(f"##### ➡ Следующая задача: {task.get('title') or 'Без названия'}")
        st.caption(f"{task.get('project')} · {task.get('status')} · приоритет {task.get('priority', 'Medium')}")
        st.caption("Почему: " + "; ".join(recommendation.reasons))


# --------------------------------------------------------------------------
# Live Execution Center (v2 Session Supervisor UI — Sprint 2 Increment 1)
#
# Thin consumer of the frozen Sprint 1 runtime (`command_center.runtime`):
# every launch/status/event/cancel operation below goes through
# `runtime_api.ExecutionCenterAPI`, never touching `Supervisor` internals,
# raw SQL, or OS signals directly. See `command_center/runtime/api.py` and
# `command_center/runtime/supervisor.py` for what those calls actually do.
# --------------------------------------------------------------------------

EXECUTION_CENTER_STATE_LABELS: dict[str, str] = {
    "PREPARED": "Подготовлен",
    "QUEUED": "В очереди",
    "RUNNING": "Выполняется",
    "COMPLETED": "Завершено",
    "FAILED": "Ошибка",
    "CANCELLED": "Отменено",
    "INTERRUPTED": "Прервано",
    "UNKNOWN": "Неизвестно",
}

# Non-terminal states — a run in one of these is still worth polling. The set
# itself lives in `runtime_db` (beside `TERMINAL_STATES`) so both `app.py` and
# Streamlit-free `command_center` modules (e.g. `workspace_home.py`) share the
# same source of truth.
EXECUTION_CENTER_ACTIVE_STATES: frozenset[str] = runtime_db.EXECUTION_CENTER_ACTIVE_STATES


@st.cache_resource
def get_execution_center_api() -> runtime_api.ExecutionCenterAPI:
    """One `ExecutionCenterAPI` (and the `Supervisor` it owns) per Streamlit
    server process, reused across every script rerun.

    A fresh `Supervisor` on every rerun would lose `Supervisor._active` — the
    in-memory registry of subprocess handles a *running* Supervisor instance
    needs to stream stdout/stderr and to signal a cancellation (see
    `supervisor.py`'s module docstring). Persisted run truth (status,
    timestamps, events) always still comes from `ExecutionCenterAPI`'s own
    reads of the runtime database, never from Streamlit session state — the
    singleton only needs to survive so cancellation keeps working, not to
    cache any data itself.

    Calls `.reconcile()` exactly once here, right after construction —
    `st.cache_resource` guarantees this runs once per server process, which
    is exactly "on app restart" for a restarted Streamlit process. This is
    the only place startup reconciliation is triggered; it reuses
    `Supervisor.reconcile()` unchanged (see `runtime/supervisor.py`) rather
    than adding any new engine or duplicating its logic.
    """
    api = runtime_api.ExecutionCenterAPI()
    api.reconcile()
    return api


def render_execution_center_launch_form(api: runtime_api.ExecutionCenterAPI) -> None:
    """Confirm-then-execute launcher for the v2 runtime, mirroring
    `render_agent_launcher`'s existing confirm/warn/disable pattern for
    BANK/LEGAL, but calling `ExecutionCenterAPI.start_run` (non-blocking:
    the Supervisor launches the subprocess and returns immediately with the
    run in state RUNNING) instead of `agent_runner.run_claude_code`
    (synchronous, blocks the whole script inside `st.spinner`)."""
    launch_project = st.selectbox("Проект", models.PROJECT_IDS, key="exec_center_launch_project")
    cfg = project_config.get_project_config(launch_project)
    repo_path = cfg.get("repository_path")
    try:
        executor_options = list(project_config.allowed_execution_providers(launch_project))
    except project_config.ProviderAuthorizationError as exc:
        st.error(str(exc))
        return
    executor_id = st.selectbox(
        "Execution provider",
        executor_options,
        format_func=lambda value: executors.get_executor(value).label,
        key="exec_center_launch_executor",
    )
    selected_executor = executors.get_executor(executor_id)
    provider_availability = selected_executor.availability
    if provider_availability is not None:
        if provider_availability.available:
            st.caption(
                f"Provider: {selected_executor.label} · available"
                + (f" · {provider_availability.version}" if provider_availability.version else "")
            )
        else:
            st.error(
                f"{selected_executor.label} unavailable ({provider_availability.code}): "
                f"{provider_availability.message}"
            )

    task_type = st.selectbox(
        "Тип задачи",
        TASK_TYPES,
        format_func=lambda value: TASK_TYPE_LABELS.get(value, value),
        key="exec_center_launch_task_type",
    )
    instruction = st.text_area(
        "Инструкция для агента", key="exec_center_launch_instruction", height=160
    )
    timeout_seconds = st.number_input(
        "Таймаут (секунды)",
        min_value=agent_runner.MIN_TIMEOUT_SECONDS,
        max_value=agent_runner.MAX_TIMEOUT_SECONDS,
        value=runtime_api.DEFAULT_TIMEOUT_SECONDS,
        step=30,
        key="exec_center_launch_timeout",
    )

    if not repo_path:
        st.error(
            f"Путь к репозиторию не настроен для проекта {launch_project}. "
            "Настройте его в разделе «Проекты» → «Настройки репозитория»."
        )
        return

    st.caption(f"Репозиторий: `{repo_path}`")

    confirmed = st.checkbox(
        f"Я подтверждаю запуск {selected_executor.label} с указанными параметрами.",
        key="exec_center_launch_confirm",
    )
    sensitivity_ack = True
    if cfg.get("sensitive"):
        st.warning(
            f"Проект {launch_project} — чувствительный (BANK/LEGAL). Дополнительный "
            "контент не прикладывается автоматически — инструкция отправляется как есть."
        )
        sensitivity_ack = st.checkbox(
            "Я подтверждаю, что не прикладываю дополнительный чувствительный контент "
            "без явного разрешения.",
            key="exec_center_launch_sensitivity_ack",
        )

    codex_target_unsafe = executor_id == "codex"
    if codex_target_unsafe:
        st.error(
            "Codex CLI requires a dedicated task worktree and intended task branch; "
            "the ad-hoc form targets the canonical project checkout. Launch Codex from a task instead."
        )
    ready = (
        confirmed
        and sensitivity_ack
        and bool(instruction.strip())
        and bool(provider_availability and provider_availability.available)
        and not codex_target_unsafe
    )
    launch_clicked = st.button(
        "Запустить",
        type="primary",
        icon=":material/play_arrow:",
        disabled=not ready,
        key="exec_center_launch_btn",
    )
    if not launch_clicked:
        return

    # Re-checked server-side, not just via the button's (client-side-only)
    # `disabled` state — this is the actual gate, matching the rest of the
    # codebase's defense-in-depth convention (e.g. `_assert_no_forbidden_flags`).
    if not ready:
        st.error("Запуск заблокирован: подтвердите все необходимые пункты перед запуском.")
        return

    conflict = launch_service.find_active_run_conflict(
        api, task_id=None, resolved_workspace=str(Path(repo_path).expanduser().resolve())
    )
    if conflict is not None:
        st.error(
            f"У workspace `{repo_path}` уже есть активный прогон (`{conflict['id']}`, "
            f"статус {conflict['state']}) — дождитесь его завершения или отмените перед новым запуском."
        )
        return

    try:
        run = api.start_run(
            project=launch_project,
            repository_path=repo_path,
            task_type=task_type,
            instruction=instruction,
            confirmed=confirmed,
            timeout_seconds=int(timeout_seconds),
            executor_id=executor_id,
        )
    except (
        runtime_context_service.ConfirmationRequiredError,
        agent_runner.RunnerError,
        project_config.ProviderAuthorizationError,
        runtime_supervisor.SupervisorError,
    ) as exc:
        st.error(str(exc))
        return

    st.success(f"Запуск создан: `{run['id']}` (статус: {EXECUTION_CENTER_STATE_LABELS.get(run['state'], run['state'])})")
    st.session_state.pending_exec_center_run = run["id"]
    st.rerun()


def _execution_center_status_badge_color(status: str) -> str:
    return {
        session_view.STATUS_LAUNCHING: "blue",
        session_view.STATUS_STARTING: "blue",
        session_view.STATUS_RUNNING: "blue",
        session_view.STATUS_STALE: "orange",
        session_view.STATUS_WAITING: "orange",
        session_view.STATUS_REQUIRES_ATTENTION: "orange",
        session_view.STATUS_BLOCKED: "red",
        session_view.STATUS_INCOMPLETE: "orange",
        session_view.STATUS_COMPLETED: "green",
        session_view.STATUS_FAILED: "red",
        session_view.STATUS_CANCELLED: "gray",
    }.get(status, "gray")


def _execution_center_display_status(session: dict) -> str:
    """`session["status"]` as computed by `session_view.derive_status`, with
    one additional UI-level guard on top: a `Completed` run whose task has not
    reached `progress == 100` is shown as `Requires Attention` — a process that
    exited but whose work never merged is not "done" (Required fix 7).

    The exception is a **read-only** task (review/audit/gate): it has no merge
    lifecycle, so a clean `COMPLETED` *is* its terminal success and its Kanban
    `progress` legitimately never reaches 100. Downgrading it to Requires
    Attention was the bug behind "a successful analysis shows as needing
    attention" — read-only completed runs stay `Completed`."""
    status = session["status"]
    progress = session.get("progress")
    if status == session_view.STATUS_COMPLETED and progress is not None and progress < 100:
        if session_view.is_read_only_task_type(session.get("task_type")):
            return status
        return session_view.STATUS_REQUIRES_ATTENTION
    return status


def _execution_center_record_heartbeat(run_id: str, pid: int | None, now: datetime) -> None:
    """Cheap, read-only liveness probe — never a signal to the process,
    never a write to `runtime.db`. This *is* the mission's "Heartbeat", and
    it is exactly what it sounds like: the last time the UI itself confirmed
    (via `identity.capture_identity`, the same primitive `Supervisor.
    reconcile()` already uses) that this PID still exists — not a signal the
    agent emits. Kept only in `st.session_state`, never persisted, so it
    never adds a row to `runtime.db` on every refresh tick."""
    if not pid:
        return
    if runtime_identity.capture_identity(pid) is not None:
        st.session_state.setdefault("exec_center_heartbeats", {})[run_id] = now


def _execution_center_heartbeat_probe_at(run_id: str) -> datetime | None:
    return st.session_state.get("exec_center_heartbeats", {}).get(run_id)


def _build_execution_center_sessions(
    api: runtime_api.ExecutionCenterAPI, tasks: list[dict], *, now: datetime
) -> tuple[list[dict], dict[str, dict]]:
    """Fetches every v2 run, joins it with its Kanban task (if any) and
    project config, and projects it through `session_view.build_session_view`
    — all business logic lives in `command_center.runtime.session_view`,
    this is just the join. Also performs the read-only heartbeat probe for
    every currently-Running run as a side effect."""
    tasks_by_id = {t["id"]: t for t in tasks if t.get("id")}
    # Clear per-render git-status cache so stale results from the previous
    # page load are discarded (many runs share the same workspace path).
    session_view.clear_git_status_cache()
    runs = api.list_runs(limit=200)
    # Batch the three per-run reads into one query each (audit H5 N+1). This loop
    # used to open ~3 fresh sqlite connections per run — up to ~600 per render —
    # every 2-5s and on every Home render; now it is 3 queries for the whole board.
    run_ids = [run["id"] for run in runs]
    latest_by_run = log_tail.latest_events_for_runs(api.db_path, run_ids)
    completion_by_run = api.get_completions_for_runs(run_ids)
    report_by_run = api.get_reports_for_runs(run_ids)
    # Historical median duration per task_type, computed once for the whole
    # board — the realistic denominator for progress/"осталось" when a task
    # carries no explicit estimate. See `session_view.median_completed_run_seconds`.
    _median_cache: dict[str | None, float | None] = {}

    def _reference_seconds(run: dict, task: dict | None) -> float | None:
        # Priority: the task's own estimate (2h → 7200 s) → historical median for
        # its type → None (caller falls back to the timeout cap). This is what
        # makes "осталено" track expected execution, not the timeout budget.
        estimate_hours = (task or {}).get("estimate_hours")
        if estimate_hours:
            return float(estimate_hours) * 3600.0
        tt = run.get("task_type")
        if tt not in _median_cache:
            _median_cache[tt] = session_view.median_completed_run_seconds(runs, task_type=tt)
        return _median_cache[tt] or session_view.median_completed_run_seconds(runs)

    sessions: list[dict] = []
    for run in runs:
        kanban_task = tasks_by_id.get(run.get("task_id"))
        project_cfg = project_config.get_project_config(run.get("project")) if run.get("project") else None
        latest = latest_by_run.get(run["id"])
        report_path = (kanban_task or {}).get("report_path")
        if not report_path:
            report_row = report_by_run.get(run["id"])
            report_path = report_row["path"] if report_row else None
        # Probe liveness *before* deriving the display status, and key it off
        # the persisted `run.state` (RUNNING) rather than the derived display
        # status — otherwise staleness (which is itself an input to the display
        # status) would be circular. A fresh probe on a live PID makes
        # `heartbeat_stale` False (age ~0); only a RUNNING run whose PID can no
        # longer be confirmed lets the last probe age past the threshold, at
        # which point the run displays `STATUS_STALE` (a warning, not a
        # failure).
        if run.get("state") == "RUNNING":
            _execution_center_record_heartbeat(run["id"], run.get("pid"), now)
        heartbeat_stale = run.get("state") == "RUNNING" and session_view.is_heartbeat_stale(
            _execution_center_heartbeat_probe_at(run["id"]), now
        )
        completion = completion_by_run.get(run["id"])
        session = session_view.build_session_view(
            run,
            kanban_task=kanban_task,
            project_cfg=project_cfg,
            latest_event=latest,
            report_path=report_path,
            now=now,
            heartbeat_stale=heartbeat_stale,
            completion=completion,
            reference_seconds=_reference_seconds(run, kanban_task),
        )
        sessions.append(session)
    return sessions, tasks_by_id


def _render_execution_center_completion(session: dict) -> None:
    """Compact "autonomous completion" panel for a session card. Reads only
    `session["completion"]` (a pure projection built in `session_view`) — no
    orchestration happens here. Clearly separates "process finished" from "task
    completed and merged", and renders safely when optional fields are missing."""
    # A read-only task (review/audit/gate) has no merge lifecycle, so any
    # completion row seeded for it by the auto-merge pipeline is spurious — its
    # "Merge заблокирован"/PR fields describe a merge that was never meant to
    # happen. Showing that panel on a successful analysis is pure confusion, so
    # it is suppressed here; the card's progress already reads 100 % «Готово».
    if session_view.is_read_only_task_type(session.get("task_type")):
        return
    completion = session.get("completion")
    if not completion:
        return
    with st.container(border=True):
        badge_color = "green" if completion["is_done"] else (
            "red" if completion["display"] == "Requires Attention" else "orange"
        )
        head = st.columns([3, 1])
        head[0].markdown("**Автономное завершение задачи**")
        head[1].badge(completion["display"], color=badge_color)
        # Queryable caption text (used by tests / screen readers) that makes the
        # process-vs-task distinction explicit.
        if completion["is_done"]:
            st.caption("Завершение: **Done** — задача завершена и смёржена в целевую ветку.")
        else:
            st.caption(
                f"Завершение: **{completion['display']}** — процесс завершён, "
                "но задача ещё не смёржена в целевую ветку."
            )
        cols = st.columns(2)
        with cols[0]:
            st.write(f"Состояние: `{completion['state'] or '—'}`")
            st.write(f"Ветка → цель: `{completion['branch'] or '—'}` → `{completion['base_branch'] or '—'}`")
            st.write(f"Коммит: `{(completion['head_commit'] or '—')[:12]}`")
            st.write(f"Валидация: {completion['validation_summary'] or '—'}")
        with cols[1]:
            pr_number = completion["pull_request_number"]
            pr_state = completion["pull_request_state"] or "—"
            if pr_number and completion["pull_request_url"]:
                st.write(f"PR: [#{pr_number}]({completion['pull_request_url']}) · {pr_state}")
            else:
                st.write(f"PR: #{pr_number or '—'} · {pr_state}")
            if completion["replaced_pull_request_number"]:
                st.write(f"Заменяет закрытый PR: #{completion['replaced_pull_request_number']}")
            st.write(f"Merge-коммит: `{(completion['merge_commit'] or '—')[:12]}`")
            st.write(f"Проверено: {completion['last_checked_at'] or '—'}")
        if completion["recommended_action"]:
            note = st.warning if (completion["requires_human"] or completion["display"] == "Requires Attention") else st.info
            note(f"Рекомендуемое действие: {completion['recommended_action']}")


_PROMPT_PREVIEW_CHARS = 700

_OPEN_TASK_DETAIL_KEY = "open_task_detail_id"


def _open_task_detail(task_id: str) -> None:
    """Request the task-detail dialog for `task_id` on the next rerun. A single
    shared trigger key so *every* view — a run card, a triage row, a tree node —
    drills into the same task detail the same way (mission: a task must be
    reachable from more than a couple of screens)."""
    st.session_state[_OPEN_TASK_DETAIL_KEY] = task_id


@st.dialog("Задача", width="large")
def _task_detail_dialog(task: dict, tasks_by_id: dict[str, dict]) -> None:
    """A compact, read-mostly task detail reachable from anywhere on the board.

    Deliberately *not* `render_task_card`: that card embeds `render_agent_launcher`,
    which opens its own `st.dialog` on launch — and Streamlit forbids a dialog
    inside a dialog. This shows the essentials (objective, live state, the
    blocking chain, history) plus a jump to the full Kanban card for editing,
    which is the one place the launcher can legally live."""
    title = task.get("title") or "Без названия"
    st.markdown(f"### {title}")
    st.caption(
        f"{task.get('project') or '—'} · "
        f"{TASK_TYPE_LABELS.get(task.get('task_type'), task.get('task_type') or '—')} · "
        f"`{task.get('id')}`"
    )

    cols = st.columns(3)
    with cols[0]:
        st.badge(task.get("priority") or "Medium", color=PRIORITY_COLORS.get(task.get("priority"), "blue"))
    with cols[1]:
        launch_status = task.get("launch_status") or "Ready"
        st.badge(launch_status, color=LAUNCH_STATUS_COLORS.get(launch_status, "gray"))
    with cols[2]:
        st.badge(task.get("status") or "—", color="gray")

    progress = int(task.get("progress") or 0)
    st.progress(progress / 100, text=f"{task.get('current_stage') or '—'} — {progress}%")

    if task.get("goal"):
        st.markdown(f"🎯 **Цель.** {task['goal']}")
    if task.get("pull_request_url"):
        st.link_button("Pull Request", task["pull_request_url"], icon=":material/merge:")

    st.markdown("**Зависимости**")
    _render_dependency_tree(task, tasks_by_id)

    st.markdown("**История**")
    render_task_timeline(task)

    footer = st.columns([2, 2, 3])
    with footer[0]:
        if st.button("В очередь", icon=":material/playlist_add:", key="task_detail_enqueue", width="stretch"):
            execution_queue.enqueue_and_persist(ROOT, task, tasks_by_id)
            st.success("Добавлено в очередь запуска.")
    with footer[1]:
        workspace_path = task.get("workspace_path") or task.get("repository_path")
        if st.button("Workspace", icon=":material/folder_open:", key="task_detail_ws",
                     disabled=not workspace_path, width="stretch"):
            ok, msg = launch.open_folder_at(workspace_path)
            (st.success if ok else st.error)(msg)
    with footer[2]:
        if st.button("Открыть на Kanban (полная карточка)", icon=":material/view_kanban:",
                     key="task_detail_kanban", type="primary", width="stretch"):
            st.session_state[_OPEN_TASK_DETAIL_KEY] = None
            st.session_state.pending_nav = "kanban"
            st.rerun()


def _maybe_open_task_detail(tasks_by_id: dict[str, dict]) -> None:
    """Open the task-detail dialog if a view requested it this run. Called once
    from the board body; the trigger key is cleared as it opens so the dialog
    does not reappear on the next fragment refresh."""
    task_id = st.session_state.get(_OPEN_TASK_DETAIL_KEY)
    if not task_id:
        return
    st.session_state[_OPEN_TASK_DETAIL_KEY] = None
    task = tasks_by_id.get(task_id)
    if task is not None:
        _task_detail_dialog(task, tasks_by_id)


def _render_execution_center_intent(session: dict) -> None:
    """The run's *intent*: what it is meant to achieve (task goal) and the
    instruction it was actually launched with (prompt).

    Rendered as plain text, never an expander, so this can appear inside a
    card that is itself nested in one — Streamlit forbids nesting expanders,
    and the attention rows on the board rely on that being safe. A long prompt
    is truncated to a readable preview with the full text one toggle away,
    because the point here is orientation, not reading a two-page brief."""
    run_id = session["run_id"]
    goal = (session.get("task_goal") or "").strip()
    prompt = (session.get("prompt") or "").strip()
    if not goal and not prompt:
        return

    if goal:
        st.markdown(f"🎯 **Цель.** {goal}")

    if not prompt:
        return

    show_key = f"exec_card_prompt_full_{run_id}"
    truncated = len(prompt) > _PROMPT_PREVIEW_CHARS

    label_bits = ["Промпт"]
    if session.get("task_type"):
        label_bits.append(f"тип `{session['task_type']}`")
    if session.get("prompt_version"):
        label_bits.append(f"версия {session['prompt_version']}")

    # Controls come *before* the text they govern: a button click is itself the
    # rerun, so a toggle rendered below the block it expands would only take
    # effect on the following interaction. Placing it first means the state is
    # already current when the block below reads it — no explicit `st.rerun`,
    # which would be wrong here anyway (this renders both inside and outside a
    # fragment, and `scope="fragment"` is only legal in one of those).
    head = st.columns([2, 1, 1])
    head[0].caption(" · ".join(label_bits))
    if truncated:
        with head[1]:
            if st.button(
                "Свернуть" if st.session_state.get(show_key, False) else "Показать целиком",
                key=f"exec_card_prompt_toggle_{run_id}",
                icon=":material/unfold_more:",
            ):
                st.session_state[show_key] = not st.session_state.get(show_key, False)
    with head[2]:
        if st.button("Копировать", key=f"exec_card_copy_prompt_{run_id}", icon=":material/content_copy:"):
            ok, msg = launch.copy_to_clipboard(prompt)
            (st.success if ok else st.error)(msg)

    show_full = st.session_state.get(show_key, False)
    body = prompt if (show_full or not truncated) else prompt[:_PROMPT_PREVIEW_CHARS].rstrip() + " …"
    st.code(body, language=None, wrap_lines=True)


def _render_execution_center_card(
    api: runtime_api.ExecutionCenterAPI,
    session: dict,
    tasks_by_id: dict[str, dict],
    *,
    now: datetime,
    rail_bucket: str | None = None,
) -> None:
    run_id = session["run_id"]
    display_status = _execution_center_display_status(session)
    with st.container(border=True):
        if rail_bucket:
            board_style.card_rail(rail_bucket)
        header_cols = st.columns([3, 1])
        header_cols[0].markdown(f"##### {session['task_title']}")
        header_cols[1].badge(display_status, color=_execution_center_status_badge_color(display_status))
        # `display_status` is repeated here as plain caption text (not just
        # the `st.badge` pill above) so the run's display status stays
        # queryable in tests and screen readers alike.
        st.caption(
            f"Статус: **{display_status}** · Проект: **{session['project_id']}** · "
            f"Executor: `{session['executor']}` · Источник: {session['launch_source']}"
        )

        # Prefer the live, run-derived progress (moves at real milestones);
        # fall back to the task's stage progress only if the run has no live
        # value to show. See `session_view.derive_live_progress`.
        bar_progress = session.get("live_progress")
        bar_stage = session.get("live_stage")
        if bar_progress is None:
            bar_progress = session.get("progress")
            bar_stage = session.get("current_stage")
        if bar_progress is not None:
            # For a still-running agent there is no true "percent done", so the
            # bar is time-based — make that explicit by showing elapsed and the
            # remaining time budget beside it (the % alone "does not reflect
            # reality"). For a terminal run, elapsed is the final duration.
            parts = [f"{bar_progress}%", bar_stage or "—"]
            elapsed = session.get("elapsed_seconds")
            if elapsed is not None:
                parts.append(f"прошло {session_view.format_elapsed(elapsed)}")
            if session["status"] in session_view.LIVE_PROCESS_DISPLAY_STATUSES:
                # "осталось" is against the *expected* duration (estimate or
                # historical median), not the timeout budget — the timeout is a
                # ~200 % cap a run rarely spends, so it read as unreal. Once a run
                # runs past its estimate, say so honestly rather than show 0.
                reference = session.get("reference_seconds")
                if reference and elapsed is not None:
                    remaining = int(reference) - int(elapsed)
                    if remaining > 0:
                        parts.append(f"осталось ~{session_view.format_elapsed(remaining)}")
                    else:
                        parts.append("дольше обычного")
            st.progress(min(max(bar_progress, 0), 100) / 100, text=" · ".join(parts))

        # Goal and prompt belong on the face of the card, not behind a button.
        # An operator judging a running agent asks "what is it trying to do"
        # first; previously the goal was nowhere in this screen and the prompt
        # was reachable only as "Копировать промпт", three clicks deep inside
        # the Logs panel — which meant it could be copied but never read.
        _render_execution_center_intent(session)

        info_cols = st.columns(2)
        with info_cols[0]:
            st.write(f"Workspace: `{session['workspace_path'] or '—'}`")
            st.write(f"Репозиторий: `{session['repository_path'] or '—'}`")
            st.write(f"Ожидаемая ветка: `{session['expected_branch'] or '—'}`")
            st.write(f"Текущая ветка: `{session['actual_branch'] or '—'}`")
            git_status = session.get("git_status")
            if git_status:
                dirty_label = "есть изменения" if git_status.get("dirty") else "чисто"
                st.caption(
                    f"Git-статус: {dirty_label} "
                    f"({git_status.get('modified_count', 0)} изменено, {git_status.get('untracked_count', 0)} новых)"
                )
        with info_cols[1]:
            st.write(f"Начат: {session['started_at'] or '—'}")
            st.write(f"Прошло: {session_view.format_elapsed(session['elapsed_seconds'])}")
            st.write(f"PID: `{session['process_id'] or '—'}`")
            if session["status"] in session_view.LIVE_PROCESS_DISPLAY_STATUSES:
                probe_at = _execution_center_heartbeat_probe_at(run_id)
                age = session_view.heartbeat_age_seconds(probe_at, now)
                stale = session_view.is_heartbeat_stale(probe_at, now)
                age_text = f"{int(age)} с назад" if age is not None else "ещё не подтверждено"
                st.write("Heartbeat (проверка живости UI, не сигнал агента): " + age_text + (" ⚠️" if stale else ""))

        # Explicitly distinguish "agent started but early output not yet
        # received" (a valid PID exists; this is NOT a start failure) from
        # "agent failed to start" (a FAILED run with no PID — rendered in the
        # Failed section with its error). This is the direct UI counterpart to
        # the mission's required distinction.
        if session["status"] == session_view.STATUS_STARTING:
            st.info(
                "Агент запущен (процесс создан, PID есть), но первый вывод ещё не получен. "
                "Это ожидание раннего вывода, а не ошибка запуска — Claude может не выдавать "
                "stdout сразу."
            )
        elif session["status"] == session_view.STATUS_STALE:
            st.warning(
                "Процесс всё ещё числится запущенным, но UI давно не подтверждал его живость "
                "(проверка живости устарела). Это предупреждение, а не отказ запуска."
            )

        if session["latest_event"]:
            st.caption(
                f"Последнее событие ({session['latest_event'].get('at') or '—'}): "
                f"{session['latest_event'].get('summary') or '—'}"
            )
        if session.get("blocker_reason"):
            st.warning(f"Причина блокировки: {session['blocker_reason']}")
        elif session["last_error"]:
            st.error(f"Последняя ошибка: {session['last_error']}")

        _render_execution_center_completion(session)

        # Localized labels (Russian) throughout — the console UI is otherwise
        # Russian, and an English row of controls in the middle of it was one of
        # the consistency defects the UX analysis called out. Widget `key=`s are
        # unchanged, so every test that drives these buttons by key still works.
        button_cols = st.columns(6)
        with button_cols[0]:
            if st.button(
                "Папка", key=f"exec_card_ws_{run_id}", icon=":material/folder_open:",
                disabled=not session["workspace_path"], help="Открыть рабочую папку",
            ):
                ok, msg = launch.open_folder_at(session["workspace_path"])
                (st.success if ok else st.error)(msg)
        with button_cols[1]:
            if st.button(
                "Терминал", key=f"exec_card_term_{run_id}", icon=":material/terminal:",
                disabled=not session["workspace_path"], help="Открыть терминал в workspace",
            ):
                ok, msg = launch.open_terminal_at(session["workspace_path"])
                (st.success if ok else st.error)(msg)
        with button_cols[2]:
            logs_key = f"exec_card_logs_open_{run_id}"
            if st.button("Логи", key=f"exec_card_logs_btn_{run_id}", icon=":material/description:"):
                st.session_state[logs_key] = not st.session_state.get(logs_key, False)
        with button_cols[3]:
            real_task = tasks_by_id.get(session["task_id"]) if session["task_id"] else None
            if st.button(
                "Задача", key=f"exec_card_task_{run_id}", icon=":material/task_alt:", disabled=real_task is None,
                help="Открыть детали задачи",
            ):
                _open_task_detail(session["task_id"])
                st.rerun()
        with button_cols[4]:
            report_key = f"exec_card_report_open_{run_id}"
            if st.button(
                "Отчёт", key=f"exec_card_report_btn_{run_id}", icon=":material/summarize:",
                disabled=not session["report_path"],
            ):
                st.session_state[report_key] = not st.session_state.get(report_key, False)
        with button_cols[5]:
            if session["status"] in session_view.LIVE_PROCESS_DISPLAY_STATUSES:
                cancel_ack = st.checkbox("Подтвердить", key=f"exec_card_cancel_ack_{run_id}")
                if st.button(
                    "Отменить", key=f"exec_card_cancel_btn_{run_id}", icon=":material/stop_circle:",
                    disabled=not cancel_ack,
                ):
                    # `disabled=` above is the primary, client-side gate, but
                    # `AppTest.click()` (and, in principle, a malformed
                    # client request) does not itself respect it — re-check
                    # `cancel_ack` server-side, the same defense-in-depth
                    # convention every other confirm-then-act control in this
                    # codebase uses, before ever calling `request_cancel`.
                    if not cancel_ack:
                        st.error("Отмена заблокирована: подтвердите отмену перед выполнением.")
                    else:
                        # The only path to `Supervisor.cancel` — signals
                        # exactly the PID+identity recorded at launch, never
                        # an arbitrary PID, never a git command.
                        try:
                            api.request_cancel(run_id, confirmed=True)
                            st.success("Запрос на отмену отправлен.")
                        except (runtime_supervisor.SupervisorError, KeyError) as exc:
                            st.error(str(exc))
                        st.rerun()

        # Logs and report render into plain bordered containers, not expanders.
        # They are already gated by their own toggle buttons, so the expander
        # added no affordance — only the constraint that this card could never
        # appear inside another expander. The board's collapsed sections depend
        # on exactly that being allowed.
        if st.session_state.get(f"exec_card_logs_open_{run_id}"):
            with st.container(border=True):
                st.markdown("**Логи и таймлайн сессии**")
                events = log_tail.tail_events(api.db_path, run_id)
                if events:
                    st.code("\n".join(log_tail.render_log_lines(events)), language=None)
                else:
                    st.caption("Логи пока недоступны.")
                timeline = log_tail.session_timeline(api.db_path, run_id)
                if timeline:
                    st.markdown("**Таймлайн (launch/cancel/completion/failure/reconciliation):**")
                    for event in timeline:
                        payload = event.get("payload") or {}
                        st.caption(f"{event.get('created_at', '—')} — {payload.get('lifecycle', event['event_type'])}")

        if st.session_state.get(f"exec_card_report_open_{run_id}"):
            with st.container(border=True):
                st.markdown("**Отчёт**")
                report_full_path = agent_runner.resolve_report_path({"report_path": session["report_path"]})
                if report_full_path is None:
                    st.warning("Путь к отчёту не проходит проверку безопасности — файл не открыт.")
                elif report_full_path.exists():
                    st.markdown(read_text(report_full_path))
                else:
                    st.caption("Файл отчёта не найден на диске.")
                if session.get("commit_hash"):
                    st.write(f"Commit: `{session['commit_hash']}`")
                if session.get("pull_request_url"):
                    st.write(f"Pull Request: {session['pull_request_url']}")


def _render_capacity_panel(api: runtime_api.ExecutionCenterAPI) -> None:
    """How loaded the machine is and how much free agent capacity remains —
    the answer to "can anything even start right now, and on whom".

    Built from the same `scheduler.build_load_snapshot` + `default_registry`
    the planner itself uses, so this panel and the autopilot can never disagree
    about how many slots are free. Read-only."""
    settings = task_pipeline.pipeline_settings.load_settings(ROOT)
    load = scheduler.build_load_snapshot(api.db_path)
    registry = scheduler.default_registry(max_concurrency=settings.max_agent_concurrency)
    summary = live_board.capacity_summary(
        running_by_agent=dict(load.running_by_agent),
        global_running=load.global_running,
        global_limit=settings.max_global_concurrency,
        agents=[(a.agent_id, a.max_concurrency, a.available) for a in registry.all()],
    )

    st.markdown("##### Загрузка")
    with st.container(border=True):
        tone = "red" if summary.saturated else ("orange" if summary.global_free == 1 else "green")
        head = st.columns([3, 2], vertical_alignment="center")
        head[0].markdown(f"**{summary.global_running} / {summary.global_limit}** прогонов")
        with head[1]:
            st.badge(
                "нет мест" if summary.saturated else f"свободно {summary.global_free}",
                color=tone,
            )
        st.progress(
            min(summary.global_running / summary.global_limit, 1.0) if summary.global_limit else 0.0,
            text=f"Свободных агентов: {summary.free_agent_count}",
        )
        for agent in summary.agents:
            if not agent.available:
                st.caption(f"🔴 `{agent.agent_id}` — недоступен")
            elif agent.free == 0:
                st.caption(f"🟠 `{agent.agent_id}` — занят {agent.used}/{agent.max_concurrency}")
            else:
                st.caption(f"🟢 `{agent.agent_id}` — {agent.used}/{agent.max_concurrency} · свободно {agent.free}")
        if summary.saturated:
            st.caption("Все места заняты — новые задачи ждут освобождения слота.")


def _short_path(path: str | None, *, keep: int = 2) -> str:
    """Last `keep` segments of a path — the part that identifies a worktree.

    The board's side column is ~25 % of the width; a full
    `/Users/…/Projects/ai-command-center-ci-review` wraps to three lines and
    tells the reader nothing the last segment does not."""
    if not path:
        return "—"
    parts = Path(path).parts
    return "…/" + "/".join(parts[-keep:]) if len(parts) > keep else path


def _render_execution_center_project_overview(sessions: list[dict], now: datetime) -> None:
    by_project: dict[str, list[dict]] = {}
    for session in sessions:
        by_project.setdefault(session["project_id"], []).append(session)
    if not by_project:
        return

    stale_run_ids = frozenset(
        s["run_id"]
        for s in sessions
        if s["status"] in session_view.LIVE_PROCESS_DISPLAY_STATUSES
        and session_view.is_heartbeat_stale(_execution_center_heartbeat_probe_at(s["run_id"]), now)
    )

    # A vertical strip, sized for the board's narrow side column. Projects are
    # standing context — "who is where, and is anything degraded" — not the
    # thing an operator acts on, so they no longer occupy a full-width row
    # above the runs that do need acting on. Degraded projects sort first:
    # if the strip is ever cut short by height, it is cut at the healthy end.
    st.markdown("##### Проекты")
    health_rank = {"Degraded": 0, "Attention": 1, "OK": 2}
    overviews = []
    for project_id in sorted(by_project):
        cfg = project_config.get_project_config(project_id)
        overviews.append(
            project_overview.build_project_overview(
                project_id, sessions=by_project[project_id], project_cfg=cfg, now=now, stale_run_ids=stale_run_ids
            )
        )
    overviews.sort(key=lambda o: (health_rank.get(o["health"], 3), o["project_id"]))

    for overview in overviews:
        health_color = {"OK": "green", "Attention": "orange", "Degraded": "red"}.get(overview["health"], "gray")
        with st.container(border=True):
            head = st.columns([2, 1])
            head[0].markdown(f"**{overview['project_id']}**")
            with head[1]:
                st.badge(overview["health"], color=health_color)
            st.caption(
                f"▶ {overview['running_count']} · ⏳ {overview['waiting_count']} · "
                f"✅ сегодня {overview['completed_today_count']}"
            )
            # Only meaningful while something of this project's is actually
            # up; on an idle project these three lines were three dashes.
            if overview["running_count"]:
                st.caption(f"`{_short_path(overview['current_workspace'])}`")
                st.caption(
                    f"{overview['current_executor'] or '—'} · ветка `{overview['current_branch'] or '—'}`"
                )

            # The side strip is navigation: picking a project opens its task
            # tree in the main column, where there is width for it. Rendering
            # the tree here instead would put fifty levelled rows into a
            # quarter-width column.
            project_id = overview["project_id"]
            selected = st.session_state.get(_PROJECT_TREE_KEY) == project_id
            if st.button(
                "Скрыть дерево" if selected else "Дерево задач",
                key=f"exec_project_tree_{project_id}",
                icon=":material/account_tree:",
                width="stretch",
                type="primary" if selected else "secondary",
            ):
                st.session_state[_PROJECT_TREE_KEY] = None if selected else project_id
                st.rerun()


# How many terminal runs the board keeps on screen. History is bounded, not
# complete: the full record lives in Журнал запусков, and a board that renders
# every run ever executed is the "простыня" this layout exists to end.
_BOARD_HISTORY_LIMIT = 20


_CONSOLE_PANEL_KEY = "exec_board_open_panel"


def _render_console_actions(tasks: list[dict], tasks_by_id: dict[str, dict]) -> None:
    """The console's action bar: create a task, see the waves, read a report —
    each as a panel that opens *here*, not as a separate page.

    Three of the app's twenty nav entries existed only to hold a form or a
    list that is consulted for a few seconds and closed. Splitting them across
    pages meant losing the execution context to file a task about the run you
    were looking at. One panel is open at a time, so the bar never becomes a
    third wall of its own."""
    open_panel = st.session_state.get(_CONSOLE_PANEL_KEY)
    labels = (
        ("create", "Создать задачу", ":material/add_task:"),
        ("waves", "Волны", ":material/waves:"),
        ("reports", "Отчёты", ":material/summarize:"),
    )
    cols = st.columns(len(labels) + 1)
    for idx, (panel, label, icon) in enumerate(labels):
        with cols[idx]:
            if st.button(
                label,
                key=f"console_panel_{panel}",
                icon=icon,
                width="stretch",
                type="primary" if open_panel == panel else "secondary",
            ):
                st.session_state[_CONSOLE_PANEL_KEY] = None if open_panel == panel else panel
                st.rerun()

    open_panel = st.session_state.get(_CONSOLE_PANEL_KEY)
    if open_panel == "create":
        _render_inline_create_task()
    elif open_panel == "waves":
        with st.container(border=True):
            waves_panel.render_waves_page(tasks, tasks_by_id, ROOT)
    elif open_panel == "reports":
        _render_inline_reports()


def _render_inline_create_task() -> None:
    """A minimal create form — project, title, type, priority, goal.

    Deliberately not the full Создать задачу page: this exists to capture a
    task the moment you see the need for it, with everything else editable on
    the task card afterwards. It commits through the same locked `create_task`
    every other creation path uses, never a snapshot write."""
    with st.container(border=True):
        st.markdown("##### Новая задача")
        with st.form("console_create_task", clear_on_submit=True):
            row = st.columns([2, 4])
            project = row[0].selectbox("Проект", models.PROJECT_IDS, key="console_create_project")
            title = row[1].text_input("Название", key="console_create_title")
            row2 = st.columns([2, 2, 2])
            task_type = row2[0].selectbox("Тип", TASK_TYPES, key="console_create_type")
            priority = row2[1].selectbox("Приоритет", PRIORITIES, index=PRIORITIES.index("Medium"),
                                         key="console_create_priority")
            status = row2[2].selectbox("Колонка", KANBAN_COLUMNS, key="console_create_status")
            goal = st.text_area("Цель", key="console_create_goal", height=80)
            if st.form_submit_button("Создать", type="primary", icon=":material/add_task:"):
                if not title.strip():
                    st.error("Название обязательно.")
                else:
                    created = create_task(
                        project, title.strip(), task_type, status, goal=goal.strip() or None, priority=priority
                    )
                    st.success(f"Создана: {created.get('title')}")


def _render_inline_reports() -> None:
    """The newest reports, readable without leaving the console.

    One button, a short list, and the report text inline — rather than a page
    that renders every report file stacked end to end."""
    with st.container(border=True):
        st.markdown("##### Отчёты")
        files = artifacts.list_markdown_files(REPORTS_DIR)
        if not files:
            st.caption("Отчётов пока нет.")
            return
        newest = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:15]
        chosen = st.selectbox(
            "Отчёт",
            newest,
            format_func=lambda p: f"{p.name} · {format_mtime(p)}",
            key="console_report_pick",
        )
        if chosen is not None:
            st.markdown(read_text(chosen))


def _render_board_summary(board: dict[str, list[dict]]) -> None:
    """One line that answers "what is the state of the machine" before any
    scrolling. Deliberately the first thing rendered — the previous layout led
    with the planner's wave, so this answer was several screens down.

    Rendered as `board_style`'s tinted, accented tiles rather than flat
    `st.metric` boxes: a running count and a failure count must not look
    identical, which four grey boxes made them."""
    board_style.stat_tiles(board)


# --------------------------------------------------------------------------
# Attention triage — turn the wall of failures into something an operator can
# act on: read one concrete reason + suggested fix per item, select several,
# and relaunch them all with one shared instruction, or hide the ones already
# handled.
# --------------------------------------------------------------------------

_ATTENTION_SELECT_KEY = "exec_attention_selected"          # set[run_id] checked now
_ATTENTION_DISMISSED_KEY = "exec_attention_dismissed"      # set[run_id] hidden this session
_ATTENTION_FLASH_KEY = "exec_attention_flash"
_ATTENTION_SHOW_HIDDEN_KEY = "exec_attention_show_hidden"


def _attention_advice(session: dict) -> tuple[str, str]:
    """(what went wrong, what to do) for one attention item — concrete, not the
    generic "Requires Attention" the status badge already says.

    Reuses the completion pipeline's own `recommended_action` and the planner's
    per-reason-code remediation vocabulary so the advice here matches what the
    autopilot panel and the launch gate say about the identical condition."""
    completion = session.get("completion") or {}
    reason = (
        session.get("blocker_reason")
        or session.get("last_error")
        or completion.get("validation_summary")
        or "Прогон остановился, не дойдя до завершения."
    )
    action = (
        completion.get("recommended_action")
        or task_pipeline.remediation_for(completion.get("reason_code"))
        or "Исправьте причину и перезапустите — можно прямо отсюда, с общей инструкцией."
    )
    return str(reason), str(action)


def _build_fix_instruction(task: dict, session: dict) -> str:
    """The instruction for an operator-requested fix relaunch: the task's own
    objective plus exactly what failed last time, and a directive to diagnose
    and fix it. No operator-typed note — the agent works out *what* to fix from
    the failure it is handed, which is the whole point of "let the AI decide".
    Same shape as `task_pipeline._rework_prompt`, human-initiated."""
    base = (task.get("prompt") or task.get("goal") or task.get("title") or "").strip()
    reason, _ = _attention_advice(session)
    lines = [
        base,
        "",
        "## Исправление (перезапуск оператором)",
        "",
        "Предыдущая попытка не дошла до завершения. Разберись, почему она упала, "
        "исправь причину и доведи задачу до состояния, в котором проверка проходит.",
    ]
    if reason:
        lines += ["", "### Что пошло не так в прошлой попытке", "", reason]
    return "\n".join(lines).strip()


def _fix_attention_sessions(
    api: runtime_api.ExecutionCenterAPI,
    sessions: list[dict],
    tasks_by_id: dict[str, dict],
) -> list[tuple[str, bool, str]]:
    """Relaunch each selected attention item as a new, operator-confirmed
    attempt carrying the shared fix instruction.

    Goes through ``launch_service.execute_agent_launch_v2`` — the same path
    the Kanban launcher uses — so it inherits executor fallback (tries the
    next available agent when the configured one is in
    ``failed_executors``), workspace provisioning + verification (the
    worktree path passes the fail-closed gate via
    ``repository_already_validated``), and the per-task timeout. The
    ``confirmed=True`` flag bypasses the planner's conservative gates
    (a ``terminal_failure`` verdict, a dirty-tree warning) that exist
    precisely because *no* human was in the loop. The one gate it never
    bypasses is the fail-closed workspace isolation in
    ``Supervisor.start_raw`` — that is a safety boundary, not a
    convenience refusal.

    Returns (task_title, ok, detail) per item, in order."""
    results: list[tuple[str, bool, str]] = []
    for session in sessions:
        title = session.get("task_title") or session.get("run_id") or "—"
        task = tasks_by_id.get(session.get("task_id"))
        if task is None:
            results.append((title, False, "Задача не найдена — возможно, это ad-hoc прогон без задачи."))
            continue
        project_id = project_config.canonical_project_id(task.get("project"))
        cfg = project_config.get_project_config(project_id)
        if cfg.get("sensitive"):
            results.append((title, False, "Чувствительный проект (BANK/LEGAL) — запускайте с карточки задачи."))
            continue
        workspace = task.get("workspace_path") or task.get("repository_path") or cfg.get("repository_path")
        if not workspace:
            results.append((title, False, "Не настроен workspace задачи."))
            continue

        # --- Executor fallback (AICC-DESKTOP-017) ------------------------
        # The configured executor is retried as-is unless it already failed to
        # *start* (``failed_executors`` — a recorded startup failure with no
        # output, e.g. an expired OAuth token), in which case we fall through to
        # the next available agent in the project's ``allowed_agents`` chain.
        # We deliberately do NOT gate the configured executor on the live
        # ``provider.availability()`` probe here: that probe shells out to the
        # provider CLI and can report ``False`` for transient reasons (a
        # daemon restarting, a probe timeout under load) that are not evidence
        # the agent cannot run this task, and in test/CI the real binary is
        # absent even though the run is faked — gating on it would block the
        # retry the operator explicitly asked for. ``select_available_executor``
        # (which does probe) is only consulted once the configured executor is
        # known to have failed to start.
        configured_executor = task.get("executor") or "claude_code"
        failed = set(task.get("failed_executors") or [])
        if configured_executor not in failed:
            selected_executor = configured_executor
        else:
            selected_executor = execution_queue.select_available_executor(task, cfg)
            if selected_executor is None:
                results.append(
                    (title, False, "ни один из разрешённых исполнителей не доступен — проверьте установку/авторизацию агентов")
                )
                continue
            original = configured_executor
            task["executor"] = selected_executor
            task.setdefault("timeline", []).append(
                {
                    "ts": models.iso_now(),
                    "type": "executor_fallback",
                    "from": original,
                    "to": selected_executor,
                    "reason": "configured executor failed to start (attention triage fix)",
                }
            )

        source_repository_path = cfg.get("repository_path")
        expected_branch = task.get("branch")
        base_branch = cfg.get("base_branch") or "main"

        try:
            run = launch_service.execute_agent_launch_v2(
                project=project_id,
                task_type=task.get("task_type") or "implementation",
                prompt=_build_fix_instruction(task, session),
                timeout_seconds=agent_runner.timeout_for_task(task),
                repository_path=Path(workspace),
                execution_center_api=api,
                confirmed=True,
                task=task,
                executor_id=selected_executor,
                expected_branch=expected_branch,
                base_branch=base_branch,
                source_repository_path=source_repository_path,
                max_global_concurrency=cfg.get("max_global_concurrency"),
            )
        except (
            launch_service.DuplicateActiveLaunchError,
            runtime_context_service.ConfirmationRequiredError,
            agent_runner.RunnerError,
            project_config.ProviderAuthorizationError,
            runtime_supervisor.SupervisorError,
            workspace_provisioning.WorkspaceVerificationError,
            runtime_supervisor.WorkspaceVerificationFailed,
        ) as exc:
            results.append((title, False, str(exc)))
            continue
        results.append((title, True, run["id"]))
    return results


def _render_attention_triage(
    api: runtime_api.ExecutionCenterAPI,
    attention: list[dict],
    tasks_by_id: dict[str, dict],
    *,
    now: datetime,
) -> None:
    """The attention bucket as a triage list: select items, relaunch them all
    with one shared fix instruction, or hide the ones already handled.

    This replaces a stack of near-identical "Requires Attention" cards — which
    told the operator a problem existed but gave them no way to act on it in
    bulk — with a worklist that answers "what do I do with these": a checkbox
    per item, one concrete reason and one suggested action per row, and a single
    instruction box that drives a confirmed relaunch of everything ticked."""
    selected: set[str] = st.session_state.setdefault(_ATTENTION_SELECT_KEY, set())
    dismissed: set[str] = st.session_state.setdefault(_ATTENTION_DISMISSED_KEY, set())

    flash = st.session_state.pop(_ATTENTION_FLASH_KEY, None)
    if flash:
        for title, ok, detail in flash:
            if ok:
                st.success(f"↻ {title}: запущено (прогон `{detail[:8]}`).")
            else:
                st.warning(f"⚠ {title}: {detail}")

    visible = [s for s in attention if s["run_id"] not in dismissed]
    hidden_count = len(attention) - len(visible)

    board_style.section_head(live_board.BUCKET_ATTENTION, len(visible))
    if not visible:
        st.caption(
            "Нет остановившихся прогонов."
            + (f" Скрыто: {hidden_count}." if hidden_count else "")
        )
        return

    shown = visible[:_BOARD_HISTORY_LIMIT]

    # Bulk action bar — select items, then relaunch them. No instruction box:
    # the agent works out what to fix from the failure carried into its prompt
    # (see `_build_fix_instruction`), which is what the operator asked for —
    # "let the AI decide". One less control, and nothing to fill in before a fix.
    with st.container(border=True):
        st.caption("Выберите задачи и нажмите «Исправить» — агент сам определит, что чинить, по причине сбоя.")
        bar = st.columns([2, 2, 2, 3])
        with bar[0]:
            if st.button("Выбрать все", key="exec_attention_select_all", width="stretch"):
                selected.update(s["run_id"] for s in shown)
                st.rerun()
        with bar[1]:
            if st.button("Снять выбор", key="exec_attention_clear_sel", width="stretch"):
                selected.clear()
                st.rerun()
        with bar[2]:
            chosen = [s for s in shown if s["run_id"] in selected]
            if st.button(
                f"Исправить ({len(chosen)})",
                key="exec_attention_fix_selected",
                type="primary",
                icon=":material/build:",
                disabled=not chosen,
                width="stretch",
            ):
                st.session_state[_ATTENTION_FLASH_KEY] = _fix_attention_sessions(
                    api, chosen, tasks_by_id
                )
                for s in chosen:
                    selected.discard(s["run_id"])
                st.rerun()
        with bar[3]:
            chosen = [s for s in shown if s["run_id"] in selected]
            if st.button(
                f"Скрыть выбранные ({len(chosen)})",
                key="exec_attention_hide_selected",
                icon=":material/visibility_off:",
                disabled=not chosen,
                width="stretch",
            ):
                dismissed.update(s["run_id"] for s in chosen)
                selected.difference_update(s["run_id"] for s in chosen)
                st.rerun()
        if hidden_count:
            st.caption(f"Скрыто в этой сессии: {hidden_count}.")
            if st.button("Показать скрытые", key="exec_attention_unhide"):
                dismissed.clear()
                st.rerun()

    for session in shown:
        _render_attention_triage_row(api, session, tasks_by_id, selected, dismissed, now=now)

    if len(visible) > _BOARD_HISTORY_LIMIT:
        st.caption(
            f"Показаны {_BOARD_HISTORY_LIMIT} из {len(visible)} — остальные в «Журнале запусков»."
        )


def _render_attention_triage_row(
    api: runtime_api.ExecutionCenterAPI,
    session: dict,
    tasks_by_id: dict[str, dict],
    selected: set[str],
    dismissed: set[str],
    *,
    now: datetime,
) -> None:
    """One triage row: checkbox, title, concrete reason + suggested action, and
    per-row Открыть / Исправить / Скрыть."""
    run_id = session["run_id"]
    status = session["display_status"]
    reason, action = _attention_advice(session)
    open_key = f"exec_attention_open_{run_id}"

    with st.container(border=True):
        board_style.card_rail(live_board.bucket_for_status(status))
        head = st.columns([1, 6, 2], vertical_alignment="center")
        with head[0]:
            checked = st.checkbox(
                "Выбрать", key=f"exec_attention_cb_{run_id}",
                value=run_id in selected, label_visibility="collapsed",
            )
            if checked:
                selected.add(run_id)
            else:
                selected.discard(run_id)
        head[1].markdown(f"**{session['task_title']}**")
        with head[2]:
            st.badge(status, color=_execution_center_status_badge_color(status))

        # `Статус: **X**` stays as queryable caption text (tests and screen
        # readers both read it), beside the badge above.
        st.caption(
            f"Статус: **{status}** · Проект: **{session['project_id'] or '—'}** · "
            f"начат {session.get('started_at') or '—'}"
        )
        # The concrete failure, in an error/warning box so it is impossible to
        # miss and stays queryable as such — then the suggested action beside it.
        reason_box = st.warning if status == session_view.STATUS_BLOCKED else st.error
        reason_box(f"Что не так: {reason}")
        st.markdown(f"🛠 **Что делать.** {action}")

        actions = st.columns([1, 1, 1, 1, 2], vertical_alignment="center")
        with actions[0]:
            if st.button("Открыть", key=f"exec_attention_toggle_{run_id}", icon=":material/unfold_more:", width="stretch"):
                st.session_state[open_key] = not st.session_state.get(open_key, False)
        with actions[1]:
            if st.button(
                "Задача", key=f"exec_attention_detail_{run_id}", icon=":material/task_alt:",
                disabled=session.get("task_id") is None, width="stretch",
            ):
                _open_task_detail(session["task_id"])
                st.rerun()
        with actions[2]:
            if st.button("Исправить", key=f"exec_attention_fix_one_{run_id}", icon=":material/build:", width="stretch"):
                st.session_state[_ATTENTION_FLASH_KEY] = _fix_attention_sessions(
                    api, [session], tasks_by_id
                )
                st.rerun()
        with actions[3]:
            if st.button("Скрыть", key=f"exec_attention_hide_one_{run_id}", icon=":material/visibility_off:", width="stretch"):
                dismissed.add(run_id)
                selected.discard(run_id)
                st.rerun()

        if st.session_state.get(open_key, False):
            _render_execution_center_card(api, session, tasks_by_id, now=now)


def _render_board_sections(
    api: runtime_api.ExecutionCenterAPI,
    board: dict[str, list[dict]],
    tasks_by_id: dict[str, dict],
    *,
    now: datetime,
) -> None:
    """The board's main column, in the operator's reading order: what is
    running, what broke, what is queued, what is finished.

    The ordering is the entire redesign. Previously this rendered seven
    equal-weight status sections below a full-width project grid and the
    planner's wave, so three live runs sat under roughly two screens of
    context — which is why "the tasks disappeared" was a reasonable thing to
    say about a dashboard that was in fact showing them."""
    live = board[live_board.BUCKET_LIVE]
    board_style.section_head(live_board.BUCKET_LIVE, len(live))
    if not live:
        st.caption("Сейчас ничего не выполняется.")
    for session in live:
        _render_execution_center_card(
            api, session, tasks_by_id, now=now, rail_bucket=live_board.BUCKET_LIVE
        )

    _render_attention_triage(api, board[live_board.BUCKET_ATTENTION], tasks_by_id, now=now)

    _render_waiting_section(api, board[live_board.BUCKET_WAITING], tasks_by_id, now=now)

    done = board[live_board.BUCKET_DONE]
    if done:
        with st.expander(f"✓ {live_board.BUCKET_TITLES[live_board.BUCKET_DONE]} ({len(done)})", expanded=False):
            for session in done[:_BOARD_HISTORY_LIMIT]:
                _render_execution_center_card(api, session, tasks_by_id, now=now)


def _render_waiting_section(
    api: runtime_api.ExecutionCenterAPI,
    waiting_sessions: list[dict],
    tasks_by_id: dict[str, dict],
    *,
    now: datetime,
) -> None:
    """"Ожидают запуска" — what is queued to run but has not started yet.

    Two distinct things end up here, and the section makes the difference
    explicit because conflating them was the confusion behind "what is this
    status and why is it always empty":

    1. **The execution queue** — tasks the operator (or a wave) put in line to
       run. `ready` ones will start as soon as an agent slot frees; `waiting`
       ones are held back by a reason (usually an unmet dependency), shown per
       row. THIS is what an operator means by "waiting to launch", and it is
       almost always the populated part.
    2. **Run-level QUEUED sessions** — a run the supervisor has prepared but not
       yet spawned. A run passes through this in milliseconds, so on its own it
       is nearly always empty — which is exactly why the old section looked
       broken.
    """
    entries = execution_queue.load_queue(ROOT)
    open_entries = [e for e in entries if e.get("state") in execution_queue.OPEN_STATES]
    open_entries.sort(key=lambda e: (e.get("state") != execution_queue.STATE_READY, e.get("added_at") or ""))

    total = len(open_entries) + len(waiting_sessions)
    board_style.section_head(live_board.BUCKET_WAITING, total)

    if total == 0:
        st.caption(
            "Очередь запуска пуста — сюда попадают задачи, поставленные в очередь "
            "(кнопкой «В очередь», волной или автопилотом) и ждущие свободного слота "
            "агента. Поставьте задачу в очередь, и она появится здесь до старта."
        )
        return

    st.caption(
        "Задачи в очереди запуска: «готова» стартует, как только освободится слот; "
        "«ждёт» — держится причиной (обычно незавершённые зависимости)."
    )
    for entry in open_entries[:_BOARD_HISTORY_LIMIT]:
        task = tasks_by_id.get(entry.get("task_id")) or {}
        title = task.get("title") or entry.get("task_id") or "—"
        is_ready = entry.get("state") == execution_queue.STATE_READY
        with st.container(border=True):
            row = st.columns([6, 2, 2], vertical_alignment="center")
            row[0].markdown(f"**{title}**")
            with row[1]:
                st.badge("готова" if is_ready else "ждёт", color="green" if is_ready else "orange")
            with row[2]:
                if st.button(
                    "Детали", key=f"exec_wait_detail_{entry.get('id')}", icon=":material/task_alt:",
                    disabled=entry.get("task_id") not in tasks_by_id, width="stretch",
                ):
                    _open_task_detail(entry["task_id"])
                    st.rerun()
            st.caption(
                f"Проект **{entry.get('project') or '—'}** · "
                + (entry.get("reason") or ("готова к запуску — ждёт свободного слота" if is_ready else "ожидает"))
            )

    if waiting_sessions:
        st.caption("Прогоны, готовящиеся к старту (обычно исчезают за секунды):")
        for session in waiting_sessions:
            _render_execution_center_card(api, session, tasks_by_id, now=now)


_LAUNCH_FLASH_KEY = "exec_board_launch_flash"
_LAUNCH_BOARD_LIMIT = 12
_PROJECT_TREE_KEY = "exec_board_project_tree"


def _render_project_tree_section(
    api: runtime_api.ExecutionCenterAPI,
    project_id: str,
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    running_task_ids: frozenset[str],
) -> None:
    """A project's whole plan as dependency levels, coloured by what each task
    is actually doing.

    This is the "open a project and see the tree" view: level 0 is what can
    start now, each level below it unlocks when the one above is merged. Colour
    carries the state — green merged, orange running, red stopped, blue ready,
    grey waiting — so the shape of the remaining work reads without reading a
    single status word.

    Ready tasks carry their own launch button, gated by the same
    `live_board.launch_gate` the launch panel uses, so a level can be started
    from the level view rather than by hunting the task down elsewhere."""
    project_tasks = [t for t in tasks if project_config.project_matches(t.get("project"), project_id)]
    if not project_tasks:
        st.info(f"У проекта {project_id} нет задач.")
        return

    nodes = live_board.project_tree(project_tasks, tasks_by_id, running_task_ids=running_task_ids)
    done, total = live_board.project_progress(nodes)

    st.markdown(f"#### 🌳 {project_id} — дерево задач")
    st.progress(done / total if total else 0.0, text=f"Смёржено {done} из {total}")

    active_runs = api.list_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES)
    current_level = None
    for node in nodes:
        if node.level != current_level:
            current_level = node.level
            level_nodes = [n for n in nodes if n.level == current_level]
            level_done = sum(1 for n in level_nodes if n.state == live_board.NODE_DONE)
            st.markdown(
                f"**Уровень {current_level}** · {level_done}/{len(level_nodes)} "
                + ("✅ пройден" if level_done == len(level_nodes) else "в работе")
            )

        task = tasks_by_id.get(node.task_id, {})
        with st.container(border=True):
            row = st.columns([5, 2, 2, 2], vertical_alignment="center")
            row[0].markdown(f"{node.mark} :{node.color}[**{node.title[:70]}**]")
            row[1].caption(f"{node.state_label} · {node.priority or '—'}")
            with row[2]:
                if st.button(
                    "Детали", key=f"exec_tree_detail_{node.task_id}", icon=":material/task_alt:",
                    help="Открыть детали задачи", width="stretch",
                ):
                    _open_task_detail(node.task_id)
                    st.rerun()
            with row[3]:
                if node.state in (live_board.NODE_READY, live_board.NODE_BLOCKED):
                    gate = live_board.launch_gate(
                        task, tasks_by_id=tasks_by_id, active_runs=active_runs
                    )
                    if st.button(
                        "Запустить",
                        key=f"exec_tree_launch_{node.task_id}",
                        icon=":material/rocket_launch:",
                        type="primary" if node.is_next else "secondary",
                        disabled=not gate.allowed,
                        width="stretch",
                        help=gate.reason if not gate.allowed else "Поставить в очередь и запустить сейчас.",
                    ):
                        recheck = live_board.launch_gate(
                            task,
                            tasks_by_id=tasks_by_id,
                            active_runs=api.list_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES),
                        )
                        if not recheck.allowed:
                            st.session_state[_LAUNCH_FLASH_KEY] = {"ok": False, "message": recheck.reason}
                        else:
                            _launch_task_from_board(api, task, tasks, tasks_by_id)
                        st.rerun()
            if node.is_next:
                st.caption("⟵ Следующая по плану: этот уровень открыт, начинать с неё.")


def _render_dependency_tree(task: dict, tasks_by_id: dict[str, dict]) -> None:
    """The task's blocking chain as indented text.

    Text rather than the Graphviz chart used on the task card: this renders
    inline under a launch button, where the question is the narrow one the
    button raises — "what is holding this, and is it done?" — and a rendered
    graph answers it slower than four indented lines. The chart remains on the
    task card for reading the shape of a neighbourhood."""
    nodes = live_board.dependency_tree(task, tasks_by_id)
    if not nodes:
        st.caption("Зависимостей нет.")
        return
    for node in nodes:
        indent = "&nbsp;" * 4 * (node.depth - 1)
        mark = "✅" if node.done else "⏳"
        st.markdown(
            f"<div style='font-size:0.82rem;opacity:0.85'>{indent}"
            f"{live_board.relation_mark(node.relation)} {mark} {html.escape(node.title[:64])} "
            f"<code>{html.escape(node.status)}</code></div>",
            unsafe_allow_html=True,
        )


def _launch_task_from_board(
    api: runtime_api.ExecutionCenterAPI,
    task: dict,
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
) -> None:
    """Enqueue this task and launch that one entry — the same locked path the
    Execution Queue panel uses, never a second launch implementation.

    `enqueue_and_persist` is idempotent per task, and `launch_ready` re-derives
    readiness under the queue lock, so this cannot double-launch a task another
    session queued in between. It also keeps the guarantee that matters most:
    an entry whose pre-flight carries warnings (dirty tree, detached HEAD) is
    *not* launched here — a board button is a batch action with no per-task
    human in the loop, exactly the case `launch_ready` refuses. The refusal and
    its reason are flashed back rather than swallowed."""
    execution_queue.enqueue_and_persist(ROOT, task, tasks_by_id)
    entries = execution_queue.reevaluate_and_persist(ROOT, tasks_by_id)
    entry = next(
        (e for e in entries if e.get("task_id") == task.get("id") and e.get("state") in execution_queue.OPEN_STATES),
        None,
    )
    if entry is None:
        st.session_state[_LAUNCH_FLASH_KEY] = {"ok": False, "message": "Задача не попала в очередь запуска."}
        return

    _, results = execution_queue.launch_ready(
        ROOT,
        entries,
        tasks,
        tasks_by_id,
        project_config.load_project_configs(),
        api,
        entry_ids=[entry["id"]],
    )
    # `launch_ready` mutates the launched task dict in place, exactly like
    # `launch_service` does; commit that with the same locked bulk upsert the
    # queue panel uses. Never a whole-snapshot write — see the note above
    # `upsert_tasks` on why `save_tasks(tasks)` does not exist here.
    upsert_tasks(tasks)
    launched = [r for r in results if r.launched]
    if launched:
        st.session_state[_LAUNCH_FLASH_KEY] = {
            "ok": True,
            "message": f"Запущено: {task.get('title') or task.get('id')}.",
        }
        return
    skipped = results[0] if results else None
    st.session_state[_LAUNCH_FLASH_KEY] = {
        "ok": False,
        "message": (skipped.message if skipped else "Запуск не выполнен.")
        + " Задача осталась в очереди — запустите её с подтверждением из карточки задачи.",
    }


def _render_launch_board(
    api: runtime_api.ExecutionCenterAPI, tasks: list[dict], tasks_by_id: dict[str, dict]
) -> None:
    """Launch a task without leaving the board, with its dependency chain and
    an honest reason whenever the button is disabled.

    The gate (`live_board.launch_gate`) reuses the autopilot planner's reason
    codes, so a task the wave calls `workspace_busy` shows the same words here.
    It is an affordance, not a safety boundary — the fail-closed checks stay in
    `launch_service`/`Supervisor.start_raw`, which is why the button being
    enabled is never treated as permission by anything downstream."""
    flash = st.session_state.pop(_LAUNCH_FLASH_KEY, None)
    if flash:
        (st.success if flash["ok"] else st.warning)(flash["message"])

    # One read of the active-run table for the whole board, rather than one per
    # rendered button.
    active_runs = api.list_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES)
    candidates = [
        task
        for task in tasks
        if (task.get("status") or "") not in ("Done",)
        and task.get("status") in ("Next", "In Progress", "Backlog", "Blocked")
    ]
    gated = [
        (task, live_board.launch_gate(task, tasks_by_id=tasks_by_id, active_runs=active_runs))
        for task in candidates
    ]
    # Launchable first, then conflicts (which clear on their own), then the
    # rest — the operator's own order of interest.
    gated.sort(key=lambda pair: (not pair[1].allowed, not pair[1].is_conflict, pair[0].get("title") or ""))
    ready_count = sum(1 for _, gate in gated if gate.allowed)

    with st.expander(f"🚀 Запуск задачи ({ready_count} готовы)", expanded=False):
        if not gated:
            st.caption("Нет задач, доступных для запуска.")
            return
        st.caption(
            "Кнопка заблокирована, когда запуск конфликтует: занятый workspace, "
            "уже активная попытка или незавершённые зависимости."
        )
        for task, gate in gated[:_LAUNCH_BOARD_LIMIT]:
            with st.container(border=True):
                row = st.columns([5, 2, 2])
                row[0].markdown(f"**{(task.get('title') or task.get('id'))[:70]}**")
                row[1].caption(f"{task.get('project') or '—'} · {task.get('priority') or '—'}")
                with row[2]:
                    if st.button(
                        "Запустить",
                        key=f"exec_board_launch_{task.get('id')}",
                        icon=":material/rocket_launch:",
                        type="primary" if gate.allowed else "secondary",
                        disabled=not gate.allowed,
                        width="stretch",
                        help=gate.reason if not gate.allowed else "Поставить в очередь и запустить сейчас.",
                    ):
                        # Re-check server-side: `disabled=` is a client-side
                        # affordance and `AppTest.click()` does not honour it.
                        # The same defense-in-depth convention as the card's
                        # cancel control.
                        recheck = live_board.launch_gate(
                            task, tasks_by_id=tasks_by_id, active_runs=api.list_runs(
                                states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES
                            )
                        )
                        if not recheck.allowed:
                            st.session_state[_LAUNCH_FLASH_KEY] = {"ok": False, "message": recheck.reason}
                        else:
                            _launch_task_from_board(api, task, tasks, tasks_by_id)
                        st.rerun()

                if not gate.allowed:
                    st.caption(f"⛔ {gate.reason} · код `{gate.code}`")
                    if gate.action:
                        st.caption(f"Что делать: {gate.action}")
                _render_dependency_tree(task, tasks_by_id)

        if len(gated) > _LAUNCH_BOARD_LIMIT:
            st.caption(f"Показаны {_LAUNCH_BOARD_LIMIT} из {len(gated)}.")


def _run_autopilot_tick(api: runtime_api.ExecutionCenterAPI):
    """One bounded `task_pipeline.tick`, or `None` if it could not even be
    attempted.

    Isolated behind a broad `except` on purpose: the autopilot is an optional,
    opt-in convenience layered on top of the Live Execution Center, and an
    unexpected fault inside it must degrade to "no autopilot this refresh"
    rather than take down the dashboard that operators use to see and cancel
    real running processes. The failure is surfaced in the panel, not
    swallowed silently."""
    try:
        return task_pipeline.tick(ROOT, api, project_config.load_project_configs())
    except Exception as exc:  # noqa: BLE001 — never let autopilot break the dashboard
        st.session_state[autopilot_panel.TICK_ERROR_KEY] = str(exc)
        return None


# How often the autopilot may actually plan, independent of how often the board
# redraws. Measured against a real database, one `task_pipeline.tick` costs
# ~540 ms while reading and rendering the entire board costs ~150 ms — so on
# the 2-5 s display refresh the pipeline owned most of every cycle, and each
# refresh blanked the page for half a second before drawing anything. That is
# what read as "the dashboard blinks" and, caught mid-tick, as "the sections
# disappeared".
#
# The two cadences are genuinely independent concerns: the display interval is
# how fresh the operator's picture is, the tick interval is how eagerly work is
# planned. Nothing is lost by planning every 15 s — the tick is idempotent,
# holds a host-wide lock, and the guide is explicit that if it is not run, the
# autopilot simply does nothing.
_PIPELINE_TICK_MIN_INTERVAL_SECONDS = 15.0
_PIPELINE_TICK_AT_KEY = "exec_center_pipeline_tick_monotonic"


def _maybe_run_autopilot_tick(api: runtime_api.ExecutionCenterAPI):
    """`_run_autopilot_tick`, rate-limited to one tick per
    `_PIPELINE_TICK_MIN_INTERVAL_SECONDS`.

    Returns `None` when the tick was skipped, which callers already treat as
    "no new wave this refresh" — the last real wave stays on screen rather
    than being replaced by an empty one.

    `time.monotonic` rather than wall-clock: this is an interval, and a clock
    adjustment must not be able to stall the autopilot or let it free-run."""
    now = time.monotonic()
    last = st.session_state.get(_PIPELINE_TICK_AT_KEY)
    if last is not None and (now - last) < _PIPELINE_TICK_MIN_INTERVAL_SECONDS:
        return None
    st.session_state[_PIPELINE_TICK_AT_KEY] = now
    return _run_autopilot_tick(api)


def _render_live_execution_center_body(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    """One refresh tick's worth of work: reconcile+sync, then re-render the
    whole dashboard from freshly-read state. Called directly (no
    auto-refresh) or from one of the fixed-interval poller fragments below.

    Reconciliation runs against a *freshly loaded* task list inside
    `tasks_repository.mutate_tasks` — never the possibly several-seconds-old
    `tasks` this function was called with — and only persists when something
    actually changed (`persist_if`), so an idle poll tick costs a lock
    acquisition (cheap, uncontended) but not a disk write. `tasks` is then
    rebound to that fresh, reconciled list for the rest of this render."""
    now = datetime.now()

    # Desktop autopilot (AICC-DESKTOP-016). The bounded pipeline tick runs from
    # *this* existing refresh checkpoint — the same one that already owns
    # reconcile-and-sync — and never from a second Supervisor, a background
    # thread, or a poller of its own. `tick` returns immediately doing nothing
    # when autopilot is not explicitly opted in (the default) or when another
    # process already holds the pipeline lock, so this call is safe to make on
    # every refresh. It subsumes the reconcile+sync and queue re-evaluation
    # below; those still run for the disabled case, which is the normal one.
    #
    # Throttled — see `_maybe_run_autopilot_tick`. The tick is the one part of a
    # refresh that costs half a second, and it runs at most once per
    # `_PIPELINE_TICK_MIN_INTERVAL_SECONDS`. The spinner is shown ONLY on those
    # rare tick refreshes, never on the frequent light ones: a spinner on every
    # 3-second poll is exactly the "страница то активна, то сереет" flicker —
    # Streamlit dims the fragment while a spinner is open, so an always-on
    # spinner greys the board on every single refresh. The light reconcile+sync
    # below is ~100 ms and needs no spinner; it re-renders without dimming.
    # No st.spinner around the tick: st.spinner dims the fragment while it is
    # open, and a dim on every throttled tick is exactly the "страница то
    # активна, то сереет" flicker operators reported (it recurred even after the
    # settings-revert fix). `_maybe_run_autopilot_tick` already self-throttles to
    # at most once per _PIPELINE_TICK_MIN_INTERVAL_SECONDS and returns None
    # (doing nothing) both when the tick is not yet due and when autopilot is not
    # opted in — so the frequent light refreshes cost nothing and never dim, and
    # the rare planning tick now runs silently in place instead of greying the
    # board.
    tick_result = _maybe_run_autopilot_tick(api)

    def _sync_mutator(fresh_tasks: list[dict]) -> tuple[list[dict], list[dict]]:
        return fresh_tasks, task_sync.reconcile_and_sync(api, fresh_tasks)

    tasks, _mutated_tasks = tasks_repository.mutate_tasks(
        ROOT, _sync_mutator, persist_if=lambda result: bool(result[1])
    )

    # Queue readiness has no poller of its own (see `execution_queue`'s
    # module docstring — no hidden scheduler); it piggybacks on this
    # existing reconcile-on-refresh-tick checkpoint instead, exactly like
    # `Supervisor.reconcile()` does. Relabels waiting/ready only — never
    # launches anything.
    execution_queue.reevaluate_and_persist(ROOT, {t["id"]: t for t in tasks if t.get("id")})

    if tick_result is not None and tick_result.ran:
        # Durable outcome for the autopilot panel (AICC-DESKTOP-017): stashing
        # it rather than rendering inline is what keeps launches/skips/merge
        # results visible across the rerun this refresh path performs.
        st.session_state[autopilot_panel.TICK_RESULT_KEY] = tick_result

    sessions, tasks_by_id = _build_execution_center_sessions(api, tasks, now=now)

    # The display status is computed once, here, and carried on the session —
    # the board buckets by it, the cards badge by it, and neither can drift
    # from the other by re-deriving it independently.
    for session in sessions:
        session["display_status"] = _execution_center_display_status(session)
    board = live_board.split_board(sessions, display_status="display_status")

    # Drop superseded attempts from the attention bucket: a task that failed
    # once and then succeeded (or is running again) must not keep its old failed
    # run sitting in "Requires Attention" — a newer run for that task already
    # moved past it (this was "the task shows as needing attention even though
    # it finished"). Only the attention bucket is filtered; the superseded run
    # still exists in the run journal and its own terminal bucket.
    superseded = live_board.superseded_run_ids(sessions)
    if superseded:
        board[live_board.BUCKET_ATTENTION] = [
            s for s in board[live_board.BUCKET_ATTENTION] if s["run_id"] not in superseded
        ]

    board_style.begin()
    _render_board_summary(board)
    _render_console_actions(tasks, tasks_by_id)

    # Wide main column for what the operator acts on; narrow side column for
    # standing context. The projects strip and the autopilot wave are context:
    # worth a glance, never worth the top of the screen.
    running_task_ids = frozenset(
        s["task_id"] for s in board[live_board.BUCKET_LIVE] if s.get("task_id")
    )

    _maybe_open_task_detail(tasks_by_id)

    main, side = st.columns([3, 1], gap="medium")
    with main:
        _render_board_sections(api, board, tasks_by_id, now=now)
        selected_project = st.session_state.get(_PROJECT_TREE_KEY)
        if selected_project:
            _render_project_tree_section(api, selected_project, tasks, tasks_by_id, running_task_ids)
        _render_launch_board(api, tasks, tasks_by_id)
    with side:
        _render_capacity_panel(api)
        _render_execution_center_project_overview(sessions, now)
        # Only a tick that actually ran replaces the wave on screen. A disabled
        # or busy tick carries no wave, and letting it through would wipe the
        # last real one — leaving the operator staring at "нет данных"
        # mid-session.
        autopilot_panel.render_autopilot_wave(
            tick_result if tick_result is not None and tick_result.ran else None,
        )

    st.session_state["exec_center_last_refreshed_at"] = now.strftime("%H:%M:%S")


# Four fixed-interval pollers (2/3/4/5s) — `st.fragment(run_every=...)`
# requires a static interval per decorated function, so a user-configurable
# interval is implemented as a small fixed set of pollers, dispatched to by
# `render_live_execution_center` below, rather than any unmanaged background
# thread or a dynamically-parameterized refresh mechanism.
@st.fragment(run_every=2.0)
def _render_live_execution_center_poll_2s(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    _render_live_execution_center_body(api, tasks)


@st.fragment(run_every=3.0)
def _render_live_execution_center_poll_3s(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    _render_live_execution_center_body(api, tasks)


@st.fragment(run_every=4.0)
def _render_live_execution_center_poll_4s(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    _render_live_execution_center_body(api, tasks)


@st.fragment(run_every=5.0)
def _render_live_execution_center_poll_5s(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    _render_live_execution_center_body(api, tasks)


_EXECUTION_CENTER_POLLERS = {
    2: _render_live_execution_center_poll_2s,
    3: _render_live_execution_center_poll_3s,
    4: _render_live_execution_center_poll_4s,
    5: _render_live_execution_center_poll_5s,
}


def render_live_execution_center(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    """Top-level Live Execution Center v2 dashboard: refresh controls,
    Project Overview row, and the 5-section session dashboard. Reconciles
    every persisted `RUNNING` row against real OS processes and syncs any
    linked Kanban task's `launch_status` on every render — see
    `task_sync.reconcile_and_sync` (always the existing `Supervisor`, never
    a second execution engine)."""
    header_cols = st.columns([1, 1, 1, 2])
    with header_cols[0]:
        auto_refresh = st.toggle(
            "Автообновление", value=st.session_state.get("exec_center_auto_refresh", True), key="exec_center_auto_refresh"
        )
    with header_cols[1]:
        interval = st.selectbox(
            "Интервал (с)",
            [2, 3, 4, 5],
            index=[2, 3, 4, 5].index(st.session_state.get("exec_center_refresh_interval", 5)),
            key="exec_center_refresh_interval",
        )
    with header_cols[2]:
        st.write("")
        refresh_clicked = st.button("Обновить сейчас", icon=":material/refresh:", key="exec_center_refresh_now")
    with header_cols[3]:
        st.write("")
        st.caption(f"Обновлено: {st.session_state.get('exec_center_last_refreshed_at') or '—'}")

    if refresh_clicked:
        st.rerun()

    # The autopilot surface renders *before* the poller fragment below, so its
    # controls stay interactive at a fixed position instead of being torn down
    # and rebuilt on every fragment refresh. It reads the tick result the
    # refresh path stashes; it never runs a tick itself.
    # Controls only. The wave is rendered inside the refresh body below, where
    # it re-renders on every tick; here it would freeze at first load.
    with st.expander("Автопилот рабочего стола", icon=":material/auto_mode:"):
        autopilot_panel.render_autopilot_controls(ROOT)

    if auto_refresh:
        _EXECUTION_CENTER_POLLERS[interval](api, tasks)
    else:
        _render_live_execution_center_body(api, tasks)

    with st.expander("Запустить новый прогон (ad-hoc, без привязки к задаче)", icon=":material/smart_toy:"):
        render_execution_center_launch_form(api)


# --------------------------------------------------------------------------
# Workspace Home — thin renderer over command_center.workspace_home's snapshot.
# No business logic beyond st.* calls; every field shown for BANK/LEGAL is
# already redacted by build_workspace_home_snapshot before it reaches here
# (see WORKSPACE_HOME_ARCHITECTURE.md §5.1/§13 — this renderer is not the
# security boundary and never receives the data that would need redacting).
# --------------------------------------------------------------------------

_WORKTREE_STATE_LABELS: dict[str, str] = {
    "unconfigured": "Путь к репозиторию не настроен",
    "invalid_path": "Путь недействителен (не существует)",
    "not_git_repo": "Путь не является git-репозиторием",
    "ok": "OK",
}


def _run_badge(run: dict) -> str:
    state = run.get("state") or run.get("status") or "—"
    label = EXECUTION_CENTER_STATE_LABELS.get(state, state) if run.get("source") == "v2" else state
    return f"[{run.get('source', '—')}] {label}"


def _quick_action_open_project(project_id: str) -> None:
    st.session_state.pending_nav = "projects"
    st.session_state.pending_project_browser = project_id


def _quick_action_new_task(project_id: str) -> None:
    st.session_state.pending_nav = "create"
    st.session_state.pending_create_project = project_id


def _quick_action_launch_run(project_id: str) -> None:
    st.session_state.pending_nav = "execution_center"
    st.session_state.pending_exec_center_project = project_id


def _quick_action_view_run(source: str, run_id: str) -> None:
    if source == "v2":
        st.session_state.pending_nav = "execution_center"
        st.session_state.pending_exec_center_run = run_id
    else:
        st.session_state.pending_nav = "runs"


def render_workspace_home_page(api: runtime_api.ExecutionCenterAPI) -> None:
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    with st.container(horizontal=True):
        st.metric("Проекты", len(snapshot["projects"]), border=True)
        st.metric("Активные прогоны", len(snapshot["active_runs"]), border=True)
        st.metric("Открытые задачи (v2)", sum(p["task_count"] for p in snapshot["projects"]), border=True)
        st.metric("Артефакты", len(snapshot["artifacts"]), border=True)
        st.metric("Отчёты", len(snapshot["reports"]), border=True)

    st.divider()
    st.markdown("#### Проекты")

    for project in snapshot["projects"]:
        project_id = project["id"]
        worktree_info = snapshot["worktrees_by_project"].get(project_id, {"state": "unconfigured", "worktrees": []})
        with st.container(border=True):
            header_cols = st.columns([3, 1, 1, 1])
            badge = " · 🔒 Чувствительный" if project["sensitive"] else ""
            header_cols[0].markdown(f"**{project['display_name']}**{badge}")
            header_cols[1].metric("Задачи (v2)", project["task_count"])
            header_cols[2].metric("Активные прогоны", project["active_run_count"])
            header_cols[3].caption(_WORKTREE_STATE_LABELS.get(worktree_info["state"], worktree_info["state"]))

            if worktree_info["state"] == "ok":
                for worktree in worktree_info["worktrees"][:5]:
                    st.caption(f"🌿 {worktree.get('branch', '—')} · `{worktree.get('head', '—')}`")
            elif worktree_info["state"] != "unconfigured":
                st.warning(_WORKTREE_STATE_LABELS.get(worktree_info["state"], worktree_info["state"]))

            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("Открыть", key=f"home_open_{project_id}", icon=":material/folder_open:", width="stretch"):
                    _quick_action_open_project(project_id)
                    st.rerun()
            with action_cols[1]:
                if st.button("Новая задача", key=f"home_new_task_{project_id}", icon=":material/add_task:", width="stretch"):
                    _quick_action_new_task(project_id)
                    st.rerun()
            with action_cols[2]:
                if st.button("Запустить прогон", key=f"home_launch_{project_id}", icon=":material/play_arrow:", width="stretch"):
                    _quick_action_launch_run(project_id)
                    st.rerun()

    st.divider()
    st.markdown("#### Активные прогоны")
    if not snapshot["active_runs"]:
        st.info("Активных прогонов нет.")
    else:
        for run in snapshot["active_runs"]:
            with st.container(border=True):
                cols = st.columns([3, 2, 2, 1])
                cols[0].write(f"**{run.get('project', '—')}** · {run.get('task_type', '—')}")
                cols[1].caption(_run_badge(run))
                cols[2].caption(f"Начат: {run.get('started_at') or '—'}")
                if cols[3].button(
                    "Открыть", key=f"home_view_active_{run.get('source')}_{run.get('run_id')}", width="stretch"
                ):
                    _quick_action_view_run(run.get("source"), run.get("run_id"))
                    st.rerun()

    st.markdown("#### Последние прогоны")
    if not snapshot["recent_runs"]:
        st.info("Прогонов пока нет.")
    else:
        for run in snapshot["recent_runs"][:10]:
            with st.container(border=True):
                cols = st.columns([3, 2, 2, 1])
                cols[0].write(f"**{run.get('project', '—')}** · {run.get('task_type', '—')}")
                cols[1].caption(_run_badge(run))
                cols[2].caption(f"Завершён: {run.get('completed_at') or '—'}")
                if cols[3].button(
                    "Открыть", key=f"home_view_recent_{run.get('source')}_{run.get('run_id')}", width="stretch"
                ):
                    _quick_action_view_run(run.get("source"), run.get("run_id"))
                    st.rerun()

    st.divider()
    left, right = st.columns(2)

    with left:
        st.markdown("#### Артефакты")
        if not snapshot["artifacts"]:
            st.info("Артефактов пока нет.")
        else:
            with st.container(border=True):
                for artifact in snapshot["artifacts"][:10]:
                    st.caption(f"{artifact.get('project', '—')} · {artifact.get('task_type') or '—'}")
            if st.button("Все артефакты", key="home_view_all_artifacts"):
                st.session_state.pending_nav = "generated"
                st.rerun()

    with right:
        st.markdown("#### Отчёты")
        if not snapshot["reports"]:
            st.info("Отчётов пока нет.")
        else:
            with st.container(border=True):
                for report in snapshot["reports"][:10]:
                    verdict = models.VERDICT_LABELS.get(report.get("verdict"), report.get("verdict") or "не определён")
                    st.caption(f"{report.get('project', '—')} · {verdict}")
            if st.button("Все отчёты", key="home_view_all_reports"):
                st.session_state.pending_nav = "reports"
                st.rerun()

    st.divider()
    st.markdown("#### Последняя активность")
    if not snapshot["recent_activity"]:
        st.info("Активности пока нет.")
    else:
        with st.container(border=True):
            for event in snapshot["recent_activity"][:15]:
                st.caption(f"{event.get('ts', '—')} — {event.get('project', '—')} — {event.get('event_type', '—')}")


# --------------------------------------------------------------------------
# Page setup
# --------------------------------------------------------------------------

# Widgets cannot have their session_state key overwritten after they have
# been instantiated in the current run. Cross-page navigation (command
# palette, agent shortcuts, workspace launcher) therefore stages its target
# values under "pending_*" keys and this block applies them before any
# matching widget is created.
_PENDING_KEY_MAP = {
    "pending_nav": "nav_page",
    "pending_create_project": "create_task_project",
    "pending_create_type": "create_task_type",
    "pending_project_browser": "project_browser_select",
    "pending_chat_conv": "chat_conv_select",
    "pending_exec_center_run": "exec_center_highlight_run",
    "pending_exec_center_project": "exec_center_launch_project",
}
for _pending_key, _target_key in _PENDING_KEY_MAP.items():
    if _pending_key in st.session_state:
        st.session_state[_target_key] = st.session_state.pop(_pending_key)

if "show_command_palette" not in st.session_state:
    st.session_state.show_command_palette = False


def _open_command_palette() -> None:
    st.session_state.show_command_palette = True


def build_commands() -> list[dict]:
    commands = [
        {"label": f"Перейти: {label}", "icon": icon, "action": ("nav", key)}
        for key, (label, icon) in NAV.items()
    ]
    commands.extend(
        {"label": f"Новая задача: {project}", "icon": ":material/add_task:", "action": ("new_task", project)}
        for project in models.PROJECT_IDS
    )
    return commands


page_key = shell.render_shell(
    page_title="AI Command Center",
    page_icon="🧭",
    sidebar_collapsed=st.session_state.get("nav_page") == "focus",
    title="🧭 AI Command Center",
    caption="Единый центр управления проектами, задачами и AI-процессами",
    nav=NAV,
    project_count=len(models.PROJECT_IDS),
    on_open_palette=_open_command_palette,
)

tasks = load_tasks()
tasks_by_id = {task["id"]: task for task in tasks}
project_configs = project_config.load_project_configs()


@st.dialog("Командная палитра", width="large")
def _command_palette_dialog() -> None:
    query = st.text_input(
        "Поиск",
        key="palette_query",
        placeholder="Введите название страницы или действие...",
        label_visibility="collapsed",
    )
    commands = build_commands()
    query_clean = query.strip().lower()
    matches = (
        [command for command in commands if query_clean in command["label"].lower()]
        if query_clean
        else commands
    )

    if not matches:
        st.caption("Ничего не найдено.")

    for index, command in enumerate(matches[:20]):
        if st.button(
            command["label"],
            key=f"palette_cmd_{index}",
            icon=command["icon"],
            width="stretch",
        ):
            kind, value = command["action"]
            if kind == "nav":
                st.session_state.pending_nav = value
            elif kind == "new_task":
                st.session_state.pending_nav = "create"
                st.session_state.pending_create_project = value
            st.session_state.show_command_palette = False
            st.rerun()


if st.session_state.show_command_palette:
    _command_palette_dialog()


# Execution Strip (UX-2a): cross-page live status bar. A polling fragment, so
# it updates every 5 s on its own without blanking the page behind it. Rendered
# before the page dispatch so it is visible on every page (pages that call
# `st.stop()` have already mounted it by then).
execution_strip.render_execution_strip(get_execution_center_api())


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def _home_greeting() -> str:
    hour = datetime.now().hour
    part = "Доброе утро" if 5 <= hour < 12 else "Добрый день" if 12 <= hour < 18 else "Добрый вечер"
    return f"{part} 👋"


def _runs_per_day(runs: list[dict], days: int = 7) -> tuple[int, ...]:
    """A real short series: runs started per day over the last `days` — the
    honest trend for a KPI sparkline (never random)."""
    today = datetime.now().date()
    buckets = [0] * days
    for r in runs:
        started = r.get("started_at")
        if not started:
            continue
        try:
            d = datetime.fromisoformat(started).date()
        except (ValueError, TypeError):
            continue
        delta = (today - d).days
        if 0 <= delta < days:
            buckets[days - 1 - delta] += 1
    return tuple(buckets)


def _run_started_date(run: dict) -> datetime | None:
    """Parse a run's ``started_at`` ISO timestamp to a date, or ``None``."""
    started = run.get("started_at")
    if not started:
        return None
    try:
        return datetime.fromisoformat(started)
    except (ValueError, TypeError):
        return None


# Windowed (sprint) run health — the honest denominator for the dashboard's
# "Здоровье проекта" gauge. The cumulative `len(runs)` denominator used before
# mixed a 200-run cap with all-time history and produced a number that drifted
# away from "how are we doing *lately*". A 7-day window tracks recent execution
# quality and is always well inside the Live Board's `limit=200` fetch.
HEALTH_WINDOW_DAYS = 7


def _window_terminal_runs(runs: list[dict], *, days: int = HEALTH_WINDOW_DAYS) -> list[dict]:
    today = datetime.now().date()
    out = []
    for r in runs:
        if r.get("state") not in runtime_db.TERMINAL_STATES:
            continue
        started = r.get("started_at")
        if not started:
            continue
        try:
            d = datetime.fromisoformat(started).date()
        except (ValueError, TypeError):
            continue
        if 0 <= (today - d).days < days:
            out.append(r)
    return out


def _window_success_rate(runs: list[dict], *, days: int = HEALTH_WINDOW_DAYS) -> int | None:
    """Success rate over terminal runs started in the last `days`. Returns
    `None` when there are no windowed terminal runs — the caller renders an
    explicit "Нет данных" empty state instead of a misleading 0%."""
    window = _window_terminal_runs(runs, days=days)
    if not window:
        return None
    completed = sum(1 for r in window if r.get("state") == "COMPLETED")
    return int(round(100 * completed / len(window)))


# Human-readable labels for the v1.2 activity log event types — the dashboard's
# "Последняя активность" card renders real lifecycle events (run_started,
# report_saved, …) instead of bare file mtimes from `gather_activity`, which
# exposed internal path names rather than anything the user did.
_ACTIVITY_LABELS: dict[str, str] = {
    "run_started": "Запущен агент",
    "run_completed": "Прогон завершён",
    "run_failed": "Прогон завершён с ошибкой",
    "run_queued": "Задача в очереди",
    "report_saved": "Сохранён отчёт",
    "task_created_from_message": "Создана задача",
    "task_moved_to_remediation": "Задача → remediation",
    "next_task_created": "Создана следующая задача",
    "manual_field_correction": "Ручная правка",
    "conversation_created": "Новый разговор",
    "message_added": "Новое сообщение",
    "verdict_extracted": "Извлечён вердикт",
}


def render_home_dashboard(api: runtime_api.ExecutionCenterAPI, tasks: list[dict]) -> None:
    """The Home dashboard from the approved design — KPI tiles, execution queue,
    project health, recent activity, a Kanban overview and quick actions, with an
    AI-Supervisor side panel. Pure presentation over the live task/run state via
    `command_center.ui.home_dashboard`; every number is real."""
    home_dashboard.inject_css()
    now = datetime.now()
    # Operator name is configurable via the AICC_OPERATOR env var — never a
    # hardcoded person. Unset → a neutral greeting with no name, so a fresh
    # install does not greet "Artyom".
    owner = os.environ.get("AICC_OPERATOR", "").strip()

    runs = api.list_runs(limit=200)
    sessions, tasks_by_id = _build_execution_center_sessions(api, tasks, now=now)
    for s in sessions:
        s["display_status"] = _execution_center_display_status(s)
    board = live_board.split_board(sessions, display_status="display_status")

    active = [t for t in tasks if t.get("status") != "Done"]
    done = [t for t in tasks if t.get("status") == "Done"]
    running = board[live_board.BUCKET_LIVE]
    needs_review = [t for t in tasks if t.get("launch_status") == "Needs Review"]
    projects_with_tasks = {t.get("project") for t in active if t.get("project")}
    attention = board[live_board.BUCKET_ATTENTION]

    greeting = f"{_home_greeting()} {owner}" if owner else _home_greeting()
    st.markdown(f"### {greeting}")
    st.caption("Вот что происходит с вашими проектами сегодня.")

    # Next-action hero (UX-2b): the one thing an operator opens the dashboard
    # for — "what should I do next" — promoted above the KPI row. The callout is
    # advisory (never creates/launches); a deep-link button jumps to the task.
    recommendation = recommend.recommend_next_task(
        tasks, active_runs=[r for r in runs if r.get("state") in runtime_db.EXECUTION_CENTER_ACTIVE_STATES]
    )
    if recommendation is not None:
        hero_task = recommendation.task
        with st.container(border=True):
            st.markdown(f"##### ➡ Следующая задача: {hero_task.get('title') or 'Без названия'}")
            st.caption(
                f"{hero_task.get('project')} · {hero_task.get('status')} · "
                f"приоритет {hero_task.get('priority', 'Medium')}"
            )
            st.caption("Почему: " + "; ".join(recommendation.reasons))
            hero_cols = st.columns([1, 1, 1, 1])
            with hero_cols[0]:
                if st.button("Открыть задачу", key="home_hero_open_task", icon=":material/task_alt:", width="stretch"):
                    st.session_state.pending_nav = "kanban"
                    st.rerun()
            with hero_cols[1]:
                if st.button("Запустить", key="home_hero_launch", icon=":material/play_arrow:", type="primary", width="stretch"):
                    st.session_state.pending_nav = "execution_center"
                    st.session_state.pending_exec_center_project = hero_task.get("project")
                    st.rerun()
    else:
        st.info("➡ Нет открытых незаблокированных задач — создайте новую.")

    # Real 24h run delta (UX-2b): runs started today vs yesterday, so the
    # "Агенты" KPI carries an honest day-over-day trend instead of a static
    # count. Both windows read from the already-loaded `runs` (limit=200).
    today = now.date()
    runs_today = sum(1 for r in runs if _run_started_date(r) and _run_started_date(r).date() == today)
    runs_yesterday = sum(
        1 for r in runs
        if _run_started_date(r) and _run_started_date(r).date().toordinal() == today.toordinal() - 1
    )
    if runs_yesterday:
        delta = runs_today - runs_yesterday
        runs_delta_txt = f"сегодня {runs_today} ({'+' if delta >= 0 else ''}{delta} к вчера)"
    else:
        runs_delta_txt = f"сегодня {runs_today}"

    # KPI sparklines removed: the four KPIs (Проекты/Агенты/Задачи/Ревью) measure
    # different things, but the old code fed the *same* `_runs_per_day` series to
    # all four — an identical trend under every tile that falsely implied each
    # metric had its own history. None of these KPIs has a genuine per-day series
    # derivable from the loaded runs, so the honest choice is no sparkline rather
    # than a duplicated, misleading one.
    home_dashboard.kpi_tiles([
        home_dashboard.Kpi("Проекты", len(projects_with_tasks),
                           "все активны" if not attention else f"{len(attention)} требуют внимания",
                           "📁", "violet", ()),
        home_dashboard.Kpi("Агенты", len(running), runs_delta_txt, "🤖", "blue", ()),
        home_dashboard.Kpi("Задачи", len(active), f"{len(done)} завершено", "✓", "green", ()),
        home_dashboard.Kpi("Ревью", len(needs_review), f"{len(needs_review)} ожидают", "★", "amber", ()),
    ])

    # Clickable KPI deep-links (UX-2b): the inert HTML tiles above cannot host
    # Streamlit click handlers, so a matching row of buttons gives every KPI a
    # real destination. Each navigates via the existing `pending_*` mechanism.
    kpi_btns = st.columns(4)
    _kpi_targets = [
        ("📁 Проекты", "projects", "home_kpi_projects"),
        ("🤖 Execution Center", "execution_center", "home_kpi_agents"),
        ("✓ Kanban", "kanban", "home_kpi_tasks"),
        ("★ Ревью (Kanban)", "kanban", "home_kpi_review"),
    ]
    for i, (label, nav, key) in enumerate(_kpi_targets):
        with kpi_btns[i]:
            if st.button(label, key=key, width="stretch", icon=":material/arrow_forward:"):
                st.session_state.pending_nav = nav
                st.rerun()

    main, side = st.columns([3, 1.2], gap="large")

    with main:
        col_q, col_h = st.columns(2, gap="medium")
        with col_q:
            home_dashboard.card_open("Очередь выполнения", "Все")
            rows = []
            for s in (running + board[live_board.BUCKET_WAITING])[:5]:
                st_disp = s["display_status"]
                acc = {"Running": "green", "Starting": "green", "Waiting": "amber",
                       "Completed": "blue"}.get(st_disp, "indigo")
                rows.append({
                    "icon": "⚙", "name": s.get("task_title") or "—", "meta": s.get("project_id") or "—",
                    "pct": s.get("live_progress"), "accent": acc,
                    "status": st_disp, "status_accent": acc,
                })
            if rows:
                home_dashboard.queue_rows(rows)
                # Clickable queue rows (UX-2b): a button per running/waiting
                # session deep-links to the Live Execution Center with that run
                # highlighted via the existing `pending_exec_center_run`.
                qbtns = st.columns(min(len(rows), 5)) if rows else None
                for i, s in enumerate((running + board[live_board.BUCKET_WAITING])[:5]):
                    if qbtns is not None:
                        with qbtns[i % len(qbtns)]:
                            if st.button(
                                f"→ {(s.get('task_title') or '—')[:14]}",
                                key=f"home_queue_open_{s['run_id']}",
                                width="stretch",
                                help="Открыть прогон в Execution Center",
                            ):
                                st.session_state.pending_nav = "execution_center"
                                st.session_state.pending_exec_center_run = s["run_id"]
                                st.rerun()
            else:
                st.caption("Сейчас ничего не выполняется — запустите агента из Execution Center.")
            home_dashboard.queue_footer(
                api.count_runs(), len(running), len(board[live_board.BUCKET_DONE]), len(board[live_board.BUCKET_ATTENTION])
            )
            home_dashboard.card_close()
            if st.button("Открыть Execution Center", key="home_open_exec", type="primary", width="stretch"):
                st.session_state.pending_nav = "execution_center"
                st.rerun()

        with col_h:
            home_dashboard.card_open("Здоровье проекта", "Детали")
            # Windowed health: success rate over terminal runs started in the
            # last 7 days (sprint window), not a cumulative blend over a
            # 200-capped run list. The old `len(running)*20` / `len(attention)*10`
            # multipliers were magic numbers dressing up counts as percentages.
            window_success = _window_success_rate(runs)
            task_ratio = int(100 * len(done) / len(tasks)) if tasks else 0
            if window_success is None:
                home_dashboard.health_gauge(0, "Нет данных за неделю", accent="slate")
            else:
                grade = "Отлично" if window_success >= 85 else "Хорошо" if window_success >= 60 else "Требует внимания"
                accent = "green" if window_success >= 85 else "blue" if window_success >= 60 else "amber"
                home_dashboard.health_gauge(window_success, grade, accent=accent)
            home_dashboard.metric_list([
                ("Задачи завершены", task_ratio, "green"),
                ("Прогоны успешны (7д)", window_success if window_success is not None else 0, "blue"),
            ])
            home_dashboard.card_close()

        col_a, col_k = st.columns(2, gap="medium")
        with col_a:
            home_dashboard.card_open("Последняя активность")
            # Real lifecycle events from the append-only activity log
            # (run_started / report_saved / manual_field_correction …) instead
            # of `gather_activity` file mtimes, which surfaced internal path
            # names rather than anything the user or agents actually did.
            act_rows = []
            for event in activity_log.load_activity(limit=6):
                label = _ACTIVITY_LABELS.get(event.get("type", ""), event.get("type", "Событие"))
                ts = event.get("ts") or ""
                when = ""
                if ts:
                    try:
                        when = datetime.fromisoformat(ts).strftime("%d.%m %H:%M")
                    except (ValueError, TypeError):
                        when = ts[:16]
                meta = " · ".join(p for p in (event.get("project"), when) if p)
                act_rows.append({"icon": "•", "name": label, "meta": meta or "—"})
            if act_rows:
                home_dashboard.simple_rows(act_rows)
            else:
                st.caption("Активности пока нет.")
            home_dashboard.card_close()

        with col_k:
            home_dashboard.card_open("Обзор Kanban", "Доска")
            accents = ["slate", "blue", "green", "amber", "violet"]
            cols = []
            for i, lane in enumerate(models.KANBAN_STATUSES):
                n = sum(1 for t in tasks if t.get("status") == lane)
                cols.append((lane, n, accents[i % len(accents)]))
            home_dashboard.kanban_overview(cols)
            home_dashboard.card_close()
            # Clickable Kanban columns (UX-2b): each lane count deep-links to the
            # Kanban board (the page-level filter defaults to "Все", so the
            # operator lands on the full board and can filter further).
            kbtns = st.columns(len(models.KANBAN_STATUSES))
            for i, lane in enumerate(models.KANBAN_STATUSES):
                with kbtns[i]:
                    if st.button(
                        f"→ {lane}",
                        key=f"home_kanban_{lane}",
                        width="stretch",
                        help="Открыть Kanban",
                    ):
                        st.session_state.pending_nav = "kanban"
                        st.rerun()

        home_dashboard.card_open("Быстрые действия")
        qa = st.columns(5)
        actions = [
            ("Новая задача", "create"), ("Запустить агента", "execution_center"),
            ("Workspace", "workspace_home"), ("Git Center", "git_center"), ("Отчёты", "reports"),
        ]
        for i, (label, nav) in enumerate(actions):
            with qa[i]:
                if st.button(label, key=f"home_qa_{nav}", width="stretch"):
                    st.session_state.pending_nav = nav
                    st.rerun()
        home_dashboard.card_close()

    with side:
        settings = task_pipeline.pipeline_settings.load_settings(ROOT)
        # Supervisor status reflects real run state, not a hardcoded 94%/"Active".
        # The gauge shows the same windowed health as the project-health card; the
        # status pill and caption describe what the supervisor is actually doing.
        window_success = _window_success_rate(runs)
        if attention:
            sup_status, sup_accent = "Требует внимания", "amber"
            sup_label = f"Автопилот {'включён' if settings.enabled else 'выключен'} — {len(attention)} прогонов требуют внимания"
        elif running:
            sup_status, sup_accent = "В работе", "green"
            sup_label = f"Автопилот {'включён' if settings.enabled else 'выключен'} — {len(running)} прогонов выполняется"
        else:
            sup_status, sup_accent = "Ожидает", "slate"
            sup_label = "Автопилот включён — ожидает задач" if settings.enabled else "Автопилот выключен"
        sup_percent = window_success if window_success is not None else 0
        home_dashboard.supervisor_status(
            sup_percent, sup_label, status=sup_status, accent=sup_accent,
        )
        home_dashboard.card_open("Проекты")
        proj_rows = []
        for p in sorted(projects_with_tasks):
            p_active = [t for t in active if project_config.project_matches(t.get("project"), p)]
            p_att = [t for t in p_active if t.get("launch_status") in ("Failed", "Requires Attention", "Blocked")]
            proj_rows.append({
                "icon": "▪", "name": p, "meta": f"{len(p_active)} активных",
                "right": "Внимание" if p_att else "OK", "right_accent": "amber" if p_att else "green",
            })
        home_dashboard.simple_rows(proj_rows or [{"icon": "▪", "name": "Нет активных проектов", "meta": ""}])
        home_dashboard.card_close()
        # Clickable project rows (UX-2b): a button per project opens it in the
        # Projects view via the existing `pending_project_browser` mechanism.
        if projects_with_tasks:
            pbtns = st.columns(min(len(sorted(projects_with_tasks)), 3))
            for i, p in enumerate(sorted(projects_with_tasks)):
                with pbtns[i % len(pbtns)]:
                    if st.button(f"→ {p}", key=f"home_proj_{p}", width="stretch", help="Открыть проект"):
                        st.session_state.pending_nav = "projects"
                        st.session_state.pending_project_browser = p
                        st.rerun()

        home_dashboard.card_open("Активные агенты")
        agent_rows = []
        for s in running[:6]:
            agent_rows.append({
                "icon": "🤖", "name": s.get("executor") or "claude_code",
                "meta": (s.get("task_title") or "")[:36],
                "right": "Running", "right_accent": "green",
            })
        home_dashboard.simple_rows(agent_rows or [{"icon": "🤖", "name": "Нет активных прогонов", "meta": ""}])
        home_dashboard.card_close()

    st.divider()
    proposals_panel.render_proposals_inbox(api, key_prefix="home_proposals")


def render_project_chat(project: str, tasks: list[dict], tasks_by_id: dict[str, dict]) -> None:
    """Project-scoped chat: conversations, provider send, save-to-report,
    convert-a-message-to-a-task, and launch-Claude-from-the-conversation.

    The project is passed in (not selected here), so this renders both as the
    'Чат' tab inside the project view and from the standalone chat page handler —
    the page is kept so an existing deep link still works, it just delegates
    here (AICC task 02661825: everything about a project lives inside it)."""
    conversations = chat_service.load_conversations()
    project_conversations = [c for c in conversations if c.get("project") == project]
    chat_cfg = project_configs[project]

    if chat_cfg["sensitive"]:
        st.warning(
            f"{project} — чувствительный проект (BANK/LEGAL). Файлы не прикрепляются "
            "автоматически — добавляйте разрешённый контекст вручную."
        )

    conv_options = ["+ Новый разговор"] + [c["id"] for c in project_conversations]
    conv_labels = {c["id"]: f"{c.get('title', '—')} · {c.get('updated_at', '—')}" for c in project_conversations}
    chosen_conv_id = st.selectbox(
        "Разговор",
        conv_options,
        format_func=lambda value: "Новый разговор" if value == "+ Новый разговор" else conv_labels.get(value, value),
        key=f"chat_conv_select_{project}",
    )

    if chosen_conv_id == "+ Новый разговор":
        new_conv_title = st.text_input(
            "Название нового разговора", key=f"chat_new_title_{project}", placeholder="Например: обсуждение архитектуры P1"
        )
        project_task_options = ["Без привязки"] + [
            task["id"] for task in tasks if project_config.project_matches(task.get("project"), project)
        ]
        link_task_id = st.selectbox(
            "Привязать к задаче (необязательно)",
            project_task_options,
            format_func=lambda value: "Без привязки" if value == "Без привязки" else task_label(tasks_by_id[value]),
            key=f"chat_link_task_{project}",
        )
        if st.button("Создать разговор", key=f"chat_create_conv_btn_{project}", icon=":material/add_comment:"):
            new_conv = models.new_conversation(
                project,
                new_conv_title.strip() or "Новый разговор",
                task_id=None if link_task_id == "Без привязки" else link_task_id,
            )
            conversations.append(new_conv)
            chat_service.save_conversations(conversations)
            activity_log.log_event(
                "conversation_created", project=project, task_id=new_conv.get("task_id"),
                conversation_id=new_conv["id"], message=new_conv["title"],
            )
            st.session_state.pending_chat_conv = new_conv["id"]
            st.rerun()
    else:
        active_conversation = chat_service.get_conversation(conversations, chosen_conv_id)
        if active_conversation is None:
            st.error("Разговор не найден.")
            return
        linked_task = tasks_by_id.get(active_conversation.get("task_id") or "")
        caption = f"Проект: {active_conversation['project']} · создан {active_conversation['created_at']}"
        if linked_task:
            caption += f" · задача: {task_label(linked_task)}"
        st.caption(caption)

        include_context = st.checkbox(
            "Включить контекст проекта в запрос провайдеру", value=True, key=f"chat_include_ctx_{active_conversation['id']}"
        )

        for message in active_conversation.get("messages", []):
            with st.chat_message("user" if message["role"] == "user" else "assistant"):
                role_label = "Вы" if message["role"] == "user" else "Ассистент"
                provider_suffix = f" · {message['provider']}" if message.get("provider") else ""
                st.caption(f"{role_label} · {message.get('created_at', '—')}{provider_suffix}")
                st.write(message["content"])
                msg_action_cols = st.columns(2)
                with msg_action_cols[0]:
                    if st.button("Сохранить в отчёты", key=f"chat_save_report_{message['id']}", icon=":material/save:"):
                        saved_path = _save_message_as_report(active_conversation, message)
                        st.success(f"Сохранено: `{saved_path.relative_to(ROOT)}`")
                with msg_action_cols[1]:
                    if st.button("Сделать задачей", key=f"chat_to_task_{message['id']}", icon=":material/add_task:"):
                        st.session_state[f"chat_convert_open_{message['id']}"] = True

                if st.session_state.get(f"chat_convert_open_{message['id']}"):
                    with st.form(f"chat_convert_form_{message['id']}"):
                        conv_task_type = st.selectbox(
                            "Тип задачи", TASK_TYPES, format_func=lambda v: TASK_TYPE_LABELS.get(v, v),
                            key=f"chat_convert_type_{message['id']}",
                        )
                        conv_objective = st.text_area(
                            "Цель задачи", value=message["content"], key=f"chat_convert_obj_{message['id']}"
                        )
                        if st.form_submit_button("Создать задачу"):
                            objective_clean = conv_objective.strip()
                            if not objective_clean:
                                st.error("Укажите цель задачи.")
                            else:
                                new_task_from_msg = create_task(
                                    active_conversation["project"],
                                    models.derive_short_title(objective_clean),
                                    conv_task_type,
                                    "Backlog",
                                    goal=objective_clean,
                                )
                                activity_log.log_event(
                                    "task_created_from_message", project=active_conversation["project"],
                                    task_id=new_task_from_msg["id"], conversation_id=active_conversation["id"],
                                    message="Задача создана из сообщения чата",
                                )
                                st.session_state[f"chat_convert_open_{message['id']}"] = False
                                st.success("Задача создана.")
                                st.rerun()

        st.divider()
        chat_providers = chat_service.available_providers()
        provider_status = {provider.name: provider.is_available() for provider in chat_providers}
        chosen_provider_name = st.selectbox(
            "Провайдер",
            [provider.name for provider in chat_providers],
            format_func=lambda name: chat_service.get_provider(name).label,
            key=f"chat_provider_{active_conversation['id']}",
        )
        provider_available, provider_reason = provider_status[chosen_provider_name]
        if provider_reason:
            st.info(provider_reason)
        if chosen_provider_name == "openai":
            st.caption("Использование OpenAI API оплачивается отдельно от подписки ChatGPT.")

        user_input = st.chat_input("Введите сообщение...", key=f"chat_input_{active_conversation['id']}")
        if user_input:
            conversations = chat_service.load_conversations()
            user_message = models.new_message("user", user_input, provider=None)
            chat_service.append_message(conversations, active_conversation["id"], user_message)
            activity_log.log_event(
                "message_added", project=active_conversation["project"], conversation_id=active_conversation["id"],
                message="Сообщение пользователя добавлено",
            )

            if chosen_provider_name != "local" and provider_available:
                context_text = _build_project_context_text(project) if include_context else ""
                updated_conversation = chat_service.get_conversation(conversations, active_conversation["id"])
                try:
                    with st.spinner("Ожидание ответа провайдера..."):
                        response_text = chat_service.get_provider(chosen_provider_name).send(
                            messages=updated_conversation["messages"],
                            project_context=context_text,
                            project_id=project,
                            repository_path=chat_cfg.get("repository_path"),
                            timeout_seconds=180,
                        )
                    conversations = chat_service.load_conversations()
                    assistant_message = models.new_message("assistant", response_text, provider=chosen_provider_name)
                    chat_service.append_message(conversations, active_conversation["id"], assistant_message)
                    activity_log.log_event(
                        "message_added", project=active_conversation["project"], conversation_id=active_conversation["id"],
                        message=f"Ответ провайдера {chosen_provider_name} добавлен",
                    )
                except Exception as exc:  # noqa: BLE001 — surfaced to the user, never crashes the page
                    st.error(f"Ошибка провайдера: {exc}")
            st.rerun()

        st.divider()
        st.markdown("##### Запустить Claude Code из этого разговора")
        last_user_message = next(
            (m["content"] for m in reversed(active_conversation.get("messages", [])) if m["role"] == "user"), ""
        )
        render_agent_launcher(
            key_prefix=f"chat_launch_{active_conversation['id']}",
            project=project,
            default_prompt=last_user_message,
            tasks=tasks,
            task_id=active_conversation.get("task_id"),
        )


def _project_audit_prompt(project: str) -> str:
    """The read-only audit brief. It must end with a machine-parsable section so
    `backlog_proposals.parse_candidate_tasks` can turn the report into tasks."""
    return (
        f"Проведи READ-ONLY аудит проекта {project} по четырём осям: архитектура, "
        "соблюдение правил/конвенций, качество кода и тестов, UX. Ничего в коде НЕ меняй. "
        "В конце отчёта ОБЯЗАТЕЛЬНО выведи секцию с заголовком '## Предлагаемые задачи', "
        "где каждым пунктом списка дай одно улучшение в формате "
        "'- **Короткий заголовок** — что и зачем сделать'. От 5 до 15 пунктов, по приоритету."
    )


def _latest_audit_report_text(project: str) -> str | None:
    """Newest project report that looks like an audit, so the Audit tab can turn
    it into candidate backlog tasks. Falls back to the newest report of any kind."""
    files = artifacts.list_markdown_files(REPORTS_DIR / project)
    audit_files = [f for f in files if any(k in f.name.lower() for k in ("audit", "architecture", "аудит"))]
    chosen = audit_files or files
    if not chosen:
        return None
    latest = max(chosen, key=lambda path: path.stat().st_mtime)
    return read_text(latest)


def _roadmap_reformat_prompt(project: str, wishes: str) -> str:
    """Brief for a roadmap rebuild. Ends with the same machine-parsable section
    the audit uses, so its output flows through the same candidate pipeline."""
    wishes_clean = (wishes or "").strip() or "(без дополнительных пожеланий — опирайся на текущее состояние проекта)"
    return (
        f"Пересобери roadmap проекта {project} с учётом пожеланий пользователя:\n{wishes_clean}\n\n"
        "Проанализируй текущее состояние (задачи, вехи, волны) и предложи обновлённый план. "
        "НЕ предлагай работу, которая уже сделана или уже есть в задачах. "
        "В конце ОБЯЗАТЕЛЬНО выведи секцию с заголовком '## Предлагаемые задачи', где каждым "
        "пунктом дай одну задачу в формате '- **Короткий заголовок** — что и зачем сделать', "
        "в порядке приоритета."
    )


def _latest_roadmap_report_text(project: str) -> str | None:
    """Newest project report that looks like a roadmap rebuild."""
    files = artifacts.list_markdown_files(REPORTS_DIR / project)
    roadmap_files = [f for f in files if any(k in f.name.lower() for k in ("roadmap", "переформат", "план"))]
    if not roadmap_files:
        return None
    latest = max(roadmap_files, key=lambda path: path.stat().st_mtime)
    return read_text(latest)


if page_key == "dashboard":
    render_home_dashboard(get_execution_center_api(), tasks)


# --------------------------------------------------------------------------
# Workspace Home
# --------------------------------------------------------------------------

elif page_key == "workspace_home":
    content_area.page_header(
        "Workspace Home",
        "Кросс-проектная сводка: репозитории, прогоны, артефакты и отчёты — "
        "в одном месте, только для чтения.",
    )
    render_workspace_home_page(get_execution_center_api())


# --------------------------------------------------------------------------
# Executive Dashboard
# --------------------------------------------------------------------------

elif page_key == "executive":
    st.subheader("Исполнительная панель")

    render_next_task_callout(
        tasks,
        active_runs=get_execution_center_api().list_runs(states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES),
    )

    active_tasks = [task for task in tasks if task.get("status") != "Done"]
    completed_tasks = [task for task in tasks if task.get("status") == "Done"]
    blocked_tasks = [task for task in active_tasks if is_blocked(task, tasks_by_id)]
    blocked_ids = {task["id"] for task in blocked_tasks}
    completion_rate = f"{(len(completed_tasks) / len(tasks) * 100):.0f}%" if tasks else "—"
    total_estimate = sum(task.get("estimate_hours", 0.0) for task in active_tasks)

    with st.container(horizontal=True):
        st.metric("Всего задач", len(tasks), border=True)
        st.metric("Активные", len(active_tasks), border=True)
        st.metric("Заблокированные", len(blocked_tasks), border=True)
        st.metric("Выполнено", f"{len(completed_tasks)} ({completion_rate})", border=True)
        st.metric("Оценка нагрузки", format_estimate(total_estimate), border=True)

    st.divider()

    left, right = st.columns([3, 2])

    with left:
        st.markdown("#### Статус проектов")
        statuses = parse_project_statuses()
        for project in models.PROJECT_IDS:
            # Canonical-id match (shared helper) so display-name tasks count
            # under their project — consistent with the Kanban lane and pill.
            project_tasks = [task for task in tasks if project_config.project_matches(task.get("project"), project)]
            p_active = sum(1 for task in project_tasks if task.get("status") != "Done")
            p_blocked = sum(1 for task in project_tasks if task["id"] in blocked_ids)
            p_done = sum(1 for task in project_tasks if task.get("status") == "Done")
            status_file = project_status_file_path(project)

            with st.container(border=True):
                header_cols = st.columns([2, 1, 1, 1])
                header_cols[0].markdown(f"**{project}**")
                header_cols[0].caption(statuses.get(project, "—"))
                header_cols[1].metric("Активн.", p_active)
                header_cols[2].metric("Блок.", p_blocked)
                header_cols[3].metric("Готово", p_done)
                st.caption(f"Статус-файл обновлён: {format_mtime(status_file)}")

    with right:
        st.markdown("#### Приоритеты активных задач")
        if active_tasks:
            priority_counts = dict.fromkeys(PRIORITIES, 0)
            for task in active_tasks:
                priority = task.get("priority", "Medium")
                priority_counts[priority] = priority_counts.get(priority, 0) + 1
            st.bar_chart(priority_counts)
        else:
            st.info("Нет активных задач.")

        st.markdown("#### Загрузка по исполнителям")
        owner_counts: dict[str, int] = {}
        for task in active_tasks:
            owner = task.get("owner") or "Не назначено"
            owner_counts[owner] = owner_counts.get(owner, 0) + 1
        if owner_counts:
            with st.container(border=True):
                for owner, count in sorted(owner_counts.items(), key=lambda item: item[1], reverse=True):
                    st.caption(f"{owner} — {count}")
        else:
            st.info("Нет активных задач.")

    st.divider()
    st.markdown("#### Заблокированные задачи")
    if not blocked_tasks:
        st.success("Заблокированных задач нет.")
    else:
        for task in blocked_tasks:
            unmet = unmet_dependencies(task, tasks_by_id)
            names = ", ".join(
                tasks_by_id[dep_id].get("title", "?")[:40] if dep_id in tasks_by_id else f"(удалена) {dep_id}"
                for dep_id in unmet
            )
            with st.container(border=True):
                st.markdown(f"**{(task.get('title') or '')[:80]}** · {task.get('project')}")
                st.caption(f"Ожидает: {names}")

    st.divider()
    st.markdown("#### Метрики запусков агентов")

    # Unified runs: v2 runtime.db (canonical) + legacy v1.2 journal merged —
    # the old `agent_runner.load_runs()` read only the v1.2 journal, which is
    # empty on installs that launch through the Execution Center, so every
    # metric below was always zero. See `command_center.runtime.runs_read`.
    exec_api = get_execution_center_api()
    exec_runs = runs_read.list_unified_runs(exec_api.db_path, root=ROOT)
    today = datetime.now().date()
    runs_today = [
        run
        for run in exec_runs
        if (run_ts := _parse_iso_ts(run.get("created_at"))) is not None
        and datetime.fromtimestamp(run_ts).date() == today
    ]
    successful_runs = [run for run in exec_runs if run.get("status") == "completed"]
    failed_runs = [run for run in exec_runs if run.get("status") in ("failed", "timed_out")]
    awaiting_remediation = [task for task in tasks if task.get("workflow_stage") == "Remediation"]
    awaiting_final_review = [task for task in tasks if task.get("workflow_stage") == "Final Review"]
    approved_for_commit = [task for task in tasks if task.get("latest_verdict") == models.VERDICT_APPROVED_FOR_COMMIT]

    with st.container(horizontal=True):
        st.metric("Запусков сегодня", len(runs_today), border=True)
        st.metric("Успешных", len(successful_runs), border=True)
        st.metric("Неудачных", len(failed_runs), border=True)
        st.metric("Ожидают исправления", len(awaiting_remediation), border=True)
        st.metric("Ожидают финальной проверки", len(awaiting_final_review), border=True)
        st.metric("Одобрено для commit", len(approved_for_commit), border=True)

    exec_left, exec_right = st.columns(2)
    with exec_left:
        st.markdown("##### Средняя длительность по агентам")
        durations_by_agent: dict[str, list[float]] = {}
        for run in exec_runs:
            duration = run.get("duration_seconds")
            if isinstance(duration, (int, float)):
                durations_by_agent.setdefault(run.get("agent", "—"), []).append(duration)
        if durations_by_agent:
            for agent_name, values in durations_by_agent.items():
                st.caption(f"{agent_name}: {sum(values) / len(values):.1f} с (n={len(values)})")
        else:
            st.caption("Запусков пока нет.")

    with exec_right:
        st.markdown("##### Открытые находки (Blocker/High)")
        open_blocker = sum(report_parser.severity_counts(run.get("parsed")).get("Blocker", 0) for run in exec_runs)
        open_high = sum(report_parser.severity_counts(run.get("parsed")).get("High", 0) for run in exec_runs)
        metric_cols = st.columns(2)
        metric_cols[0].metric("Blocker", open_blocker)
        metric_cols[1].metric("High", open_high)


# --------------------------------------------------------------------------
# Task creator
# --------------------------------------------------------------------------

elif page_key == "create":
    st.subheader("Создание AI-задачи")

    open_tasks = [task for task in tasks if task.get("status") != "Done"]

    # `project` lives outside `create_task_form` on purpose: its value must be
    # available immediately (a form's inner widgets don't rerun the script
    # until submitted) so the inherited-defaults preview below reacts to the
    # project the user just picked, before they submit anything.
    project = st.selectbox("Проект", models.PROJECT_IDS, key="create_task_project")
    create_task_cfg = project_configs[project]
    inherited = project_config.task_defaults_from_project(create_task_cfg)

    st.caption(
        f"Унаследовано из настроек проекта «{create_task_cfg['display_name']}»: "
        f"workspace `{inherited['workspace_path'] or '—'}` · "
        f"branch `{inherited['branch'] or '—'}` · "
        f"executor `{inherited['executor'] or '—'}` · "
        f"prompt {'задан' if inherited['prompt'] else '—'}. "
        "Изменить можно в разделе «Проекты» → «Настройки проекта», либо переопределить ниже только для этой задачи."
    )

    with st.expander("Переопределить workspace / branch / executor / prompt для этой задачи"):
        override_workspace = st.text_input(
            "Workspace (переопределение)",
            placeholder=inherited["workspace_path"] or "унаследовано из проекта",
            key="create_task_workspace_override",
        )
        override_branch = st.text_input(
            "Branch (переопределение)",
            placeholder=inherited["branch"] or "унаследовано из проекта",
            key="create_task_branch_override",
        )
        executor_override_options = ["(унаследовано из проекта)"] + executors.EXECUTOR_IDS
        override_executor = st.selectbox(
            "Executor (переопределение)",
            executor_override_options,
            key="create_task_executor_override",
        )
        override_prompt = st.text_area(
            "Prompt (переопределение)",
            placeholder=inherited["prompt"] or "унаследовано из проекта",
            key="create_task_prompt_override",
        )

    with st.form("create_task_form"):
        title_input = st.text_input(
            "Название задачи",
            placeholder="Короткий заголовок, например: Исправить сортировку в Kanban",
            key="create_task_title",
        )
        task_type = st.selectbox(
            "Тип задачи",
            TASK_TYPES,
            format_func=lambda value: TASK_TYPE_LABELS.get(value, value),
            key="create_task_type",
        )
        objective = st.text_area(
            "Цель задачи",
            height=160,
            placeholder="Например: проверить текущий статус AIOS и определить следующую задачу",
            key="create_task_objective",
        )
        notes = st.text_area(
            "Заметки (необязательно)",
            height=80,
            placeholder="Свободные заметки, независимые от цели и промпта",
            key="create_task_notes",
        )

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            priority = st.selectbox("Приоритет", PRIORITIES, index=1, key="create_task_priority")
        with col_b:
            owner = st.text_input("Исполнитель", placeholder="Например: Дмитрий", key="create_task_owner")
        with col_c:
            estimate = st.number_input(
                "Оценка (часы)", min_value=0.0, step=0.5, value=0.0, key="create_task_estimate"
            )

        dependencies = st.multiselect(
            "Зависит от",
            options=[task["id"] for task in open_tasks],
            format_func=lambda task_id: task_label(tasks_by_id[task_id]),
            key="create_task_deps",
        )

        initial_status = st.selectbox("Статус Kanban", KANBAN_COLUMNS, key="create_task_status")
        submitted = st.form_submit_button(
            "Создать задачу",
            icon=":material/add_task:",
            type="primary",
            key="create_task_form_submit",
        )

    if submitted:
        title_clean = title_input.strip()
        objective_clean = objective.strip()

        if not title_clean:
            st.error("Укажите название задачи.")
        elif not objective_clean:
            st.error("Укажите цель задачи.")
        elif project not in models.PROJECT_IDS:
            st.error("Неизвестный проект.")
        elif task_type not in TASK_TYPES:
            st.error("Неизвестный тип задачи.")
        else:
            with st.spinner("Выполняется scripts/start-task.sh..."):
                ok, stdout, stderr = run_start_task_script(project, task_type, objective_clean)

            if ok:
                final_executor = (
                    None if override_executor == "(унаследовано из проекта)" else override_executor
                )
                create_task(
                    project,
                    title_clean,
                    task_type,
                    initial_status,
                    goal=objective_clean,
                    notes=notes.strip(),
                    priority=priority,
                    owner=owner.strip(),
                    estimate_hours=float(estimate),
                    depends_on=dependencies,
                    workspace_path=override_workspace.strip() or inherited["workspace_path"],
                    branch=override_branch.strip() or inherited["branch"],
                    executor=final_executor or inherited["executor"],
                    prompt=override_prompt.strip() or inherited["prompt"],
                )
                st.success(f"Задача создана и добавлена в Kanban (статус «{initial_status}»).")
                if stdout:
                    with st.expander("Вывод скрипта"):
                        st.code(stdout, language=None)
            else:
                st.error("Не удалось выполнить scripts/start-task.sh.")
                details = stderr or stdout
                if details:
                    with st.expander("Подробности ошибки", expanded=True):
                        st.code(details, language=None)

    st.divider()
    st.markdown("#### Импорт пакета задач")
    st.caption(
        "Загрузите JSON-файл со списком задач (например, пакет от Founder-аудита) — "
        "поддерживаются как «конверт» `{schema_version, package_id, tasks}`, так и "
        "простой список задач. Ничего не записывается в `data/tasks.json` до нажатия "
        "«Импортировать задачи»."
    )
    uploaded_package = st.file_uploader(
        "JSON-пакет задач", type=["json"], key="import_task_package_uploader"
    )
    if uploaded_package is not None:
        try:
            parsed_package = task_import.parse_task_package(uploaded_package.getvalue())
        except task_import.TaskImportError as exc:
            st.error(f"Ошибка разбора пакета: {exc}")
        else:
            import_validation = task_import.validate_task_package(parsed_package)
            import_preview = task_import.build_import_preview(ROOT, parsed_package, import_validation)

            info_cols = st.columns(5)
            info_cols[0].metric("Всего задач", import_preview.total_tasks)
            info_cols[1].metric("Новые", len(import_preview.new_items))
            info_cols[2].metric("Дубликаты", len(import_preview.duplicate_ids))
            info_cols[3].metric("Ошибки", len(import_preview.errors))
            info_cols[4].metric("Предупреждения", len(import_preview.warnings))
            st.caption(
                f"Package id: `{import_preview.package_id}` · schema: `{import_preview.schema_version}` · "
                f"hash: `{import_preview.package_hash}`"
            )

            if import_preview.rows:
                st.dataframe(
                    [
                        {
                            "ID": row.id,
                            "Импорт": row.outcome,
                            "Проект": row.project,
                            "Kanban": row.status,
                            "Приоритет": row.priority,
                            "Тип": row.task_type,
                            "Название": row.title,
                        }
                        for row in import_preview.rows
                    ],
                    hide_index=True,
                    width="stretch",
                )

            for issue in import_preview.errors:
                st.error(f"[{issue.task_ref or '—'}] {issue.message}")
            for issue in import_preview.warnings:
                st.warning(f"[{issue.task_ref or '—'}] {issue.message}")

            if import_preview.has_blocking_errors:
                st.error("Пакет содержит ошибки валидации — импорт заблокирован.")
            elif not import_preview.new_items:
                st.info("Нет новых задач для импорта — все задачи пакета уже присутствуют в хранилище.")
            elif st.button(
                f"Импортировать задачи ({len(import_preview.new_items)} новых)",
                key="import_task_package_confirm_btn",
                type="primary",
                icon=":material/publish:",
            ):
                try:
                    import_result = task_import.apply_task_package(ROOT, parsed_package, import_validation)
                except task_import.TaskImportError as exc:
                    # Re-checked fresh under lock inside `apply_task_package` — can
                    # still fail here even though the preview above looked clean,
                    # e.g. a concurrent import claimed a dependency's id, or the
                    # lock timed out. Surfaced as an ordinary page error, never an
                    # uncaught exception; nothing was written in either case.
                    st.error(f"Импорт не выполнен: {exc}")
                else:
                    st.success(
                        f"Импортировано задач: {len(import_result.imported_ids)}. "
                        f"Пропущено дубликатов: {len(import_result.skipped_duplicate_ids)}."
                    )
                    st.rerun()


# --------------------------------------------------------------------------
# Project Chat
# --------------------------------------------------------------------------

elif page_key == "chat":
    st.subheader("Чат по проекту")
    # The chat now lives as the 'Чат' tab inside the project view; this page is
    # kept so an existing deep link still resolves, and delegates to the same
    # `render_project_chat` the tab uses. (task 02661825)
    st.caption("Чат теперь встроен во вкладку «Чат» страницы «Проекты».")
    chat_project = st.selectbox("Проект", models.PROJECT_IDS, key="chat_project_select")
    render_project_chat(chat_project, tasks, tasks_by_id)


# --------------------------------------------------------------------------
# Kanban board
# --------------------------------------------------------------------------

elif page_key == "waves":
    project_filter = project_selector.render_project_selector(tasks, key="waves_project_selector")
    waves_panel.render_waves_page(tasks, tasks_by_id, ROOT, project=project_filter)

elif page_key == "kanban":
    st.subheader("Kanban")

    project_filter = project_selector.render_project_selector(tasks, key="kanban_project_selector")
    project_intelligence_panel.render_project_intelligence_strip(tasks, project=project_filter)
    st.divider()

    project_configs = project_config.load_project_configs()
    recommendations_panel.render_recommendations_panel(
        tasks,
        tasks_by_id,
        ROOT,
        get_execution_center_api(),
        project_configs,
        upsert_tasks,
        project=project_filter,
        key_prefix="kanban_reco",
    )
    st.divider()

    backlog_reconcile_panel.render_backlog_reconcile_panel(
        tasks, ROOT, project=project_filter, key_prefix="kanban_reconcile"
    )
    st.divider()

    # Options come from the tasks themselves (canonical priorities + any
    # extra value actually in use, e.g. an imported `P0`), not just the
    # canonical PRIORITIES — otherwise a task whose priority is outside the
    # canonical set is neither selectable nor matched by the default
    # all-selected filter, and silently disappears from every lane (this is
    # exactly why AICC-CI-001, priority `P0`, was missing). See
    # `task_view.kanban_priority_options`.
    priority_options = task_view.kanban_priority_options(tasks)
    priority_filter = st.multiselect(
        "Приоритет", priority_options, default=priority_options, key="kanban_priority_filter"
    )

    filtered_tasks = task_view.filter_kanban_tasks(
        tasks, project=project_filter, priorities=priority_filter
    )

    kanban_git_status_cache: dict[str, dict] = {}

    # Полноширинные вертикальные дорожки Kanban.
    # Горизонтальная разметка через st.columns сжимала карточки
    # и делала заголовки и элементы управления нечитаемыми.
    for status in KANBAN_COLUMNS:
        with st.container(border=True):
            status_tasks = [task for task in filtered_tasks if task.get("status") == status]
            st.markdown(f"**{status}**")
            st.caption(f"{len(status_tasks)} задач")

            if not status_tasks:
                st.caption("Пусто")

            for task in status_tasks:
                render_task_card(
                    task,
                    tasks=tasks,
                    tasks_by_id=tasks_by_id,
                    key_prefix=f"kanban_{task.get('id')}",
                    git_status_cache=kanban_git_status_cache,
                    show_kanban_controls=True,
                )

    st.divider()
    queue_panel.render_execution_queue_panel(
        tasks,
        tasks_by_id,
        ROOT,
        get_execution_center_api(),
        project_configs,
        upsert_tasks,
        project=project_filter,
        key_prefix="kanban_queue",
    )


# --------------------------------------------------------------------------
# AI Agents
# --------------------------------------------------------------------------

elif page_key == "agents":
    st.subheader("AI-агенты")
    st.caption("Каталог типов задач, поддерживаемых scripts/start-task.sh")

    generated_files = artifacts.list_markdown_files(GENERATED_DIR)

    for task_type in TASK_TYPES:
        meta = AGENT_ROLES[task_type]
        type_tasks = [task for task in tasks if task.get("task_type") == task_type]
        active_count = sum(1 for task in type_tasks if task.get("status") != "Done")
        done_count = len(type_tasks) - active_count
        generated_count = sum(1 for path in generated_files if artifacts.infer_task_type_from_filename(path) == task_type)

        with st.container(border=True):
            st.markdown(f"### {meta['title']}")
            st.caption(f"`{task_type}` · {meta['summary']}")

            metric_cols = st.columns(3)
            metric_cols[0].metric("Активные задачи", active_count)
            metric_cols[1].metric("Завершено", done_count)
            metric_cols[2].metric("Сгенерировано файлов", generated_count)

            with st.expander("Правила выполнения"):
                for rule in meta["rules"]:
                    st.markdown(f"- {rule}")

            if st.button(
                f"Создать задачу «{meta['title']}»",
                key=f"agent_create_{task_type}",
                icon=":material/add_task:",
            ):
                st.session_state.pending_nav = "create"
                st.session_state.pending_create_type = task_type
                st.rerun()

            with st.expander(f"Запустить «{meta['title']}» напрямую", icon=":material/smart_toy:"):
                agent_launch_project = st.selectbox(
                    "Проект", models.PROJECT_IDS, key=f"agent_launch_project_{task_type}"
                )
                agent_launch_objective = st.text_area(
                    "Цель задачи",
                    key=f"agent_launch_objective_{task_type}",
                    height=120,
                    placeholder="Опишите, что должен сделать агент",
                )
                render_agent_launcher(
                    key_prefix=f"agents_page_launch_{task_type}",
                    project=agent_launch_project,
                    default_prompt=agent_launch_objective,
                    tasks=tasks,
                    default_task_type=task_type,
                )


# --------------------------------------------------------------------------
# Live Execution Center (v2 Session Supervisor)
# --------------------------------------------------------------------------

elif page_key == "execution_center":
    st.subheader("Live Execution Center")
    st.caption(
        "Канонический монитор выполнения: реальные PID-отслеживаемые прогоны через "
        "v2 Session Supervisor (command_center.runtime) — источник истины для статуса "
        "выполнения, сверяемый с реальными OS-процессами при каждом обновлении."
    )

    render_live_execution_center(get_execution_center_api(), tasks)


# --------------------------------------------------------------------------
# Run journal
# --------------------------------------------------------------------------

elif page_key == "runs":
    st.subheader("Журнал запусков")

    # Unified runs (v2 runtime.db + legacy v1.2 journal) — the old
    # `agent_runner.load_runs()` read only the v1.2 journal, which is empty on
    # installs that launch through the Execution Center, so the whole page was
    # blank. See `command_center.runtime.runs_read`.
    all_runs = runs_read.list_unified_runs(get_execution_center_api().db_path, root=ROOT)

    filter_cols = st.columns(4)
    with filter_cols[0]:
        runs_project_filter = st.selectbox("Проект", ["Все"] + models.PROJECT_IDS, key="runs_project_filter")
    with filter_cols[1]:
        runs_agent_filter = st.selectbox(
            "Агент", ["Все"] + sorted({run.get("agent", "—") for run in all_runs}), key="runs_agent_filter"
        )
    with filter_cols[2]:
        runs_status_filter = st.multiselect(
            "Статус", models.RUN_STATUSES, default=models.RUN_STATUSES,
            format_func=lambda v: models.RUN_STATUS_LABELS.get(v, v), key="runs_status_filter",
        )
    with filter_cols[3]:
        verdict_choices = list(models.VERDICT_LABELS.keys())
        runs_verdict_filter = st.multiselect(
            "Вердикт", verdict_choices, default=verdict_choices,
            format_func=lambda v: models.VERDICT_LABELS.get(v, v), key="runs_verdict_filter",
        )

    date_cols = st.columns(2)
    with date_cols[0]:
        runs_date_from = st.date_input("С даты", value=None, key="runs_date_from")
    with date_cols[1]:
        runs_date_to = st.date_input("По дату", value=None, key="runs_date_to")

    task_choices = ["Все"] + sorted({run.get("task_id") for run in all_runs if run.get("task_id")})
    runs_task_filter = st.selectbox(
        "Задача", task_choices,
        format_func=lambda v: "Все" if v == "Все" else task_label(tasks_by_id.get(v, {"project": "—", "title": v, "status": "—"})),
        key="runs_task_filter",
    )

    def _run_matches_filters(run: dict) -> bool:
        if runs_project_filter != "Все" and run.get("project") != runs_project_filter:
            return False
        if runs_agent_filter != "Все" and run.get("agent") != runs_agent_filter:
            return False
        if run.get("status") not in runs_status_filter:
            return False
        run_verdict = (run.get("parsed") or {}).get("verdict")
        if run_verdict and run_verdict not in runs_verdict_filter:
            return False
        if runs_task_filter != "Все" and run.get("task_id") != runs_task_filter:
            return False
        created_ts = _parse_iso_ts(run.get("created_at"))
        if created_ts is not None:
            created_date = datetime.fromtimestamp(created_ts).date()
            if runs_date_from and created_date < runs_date_from:
                return False
            if runs_date_to and created_date > runs_date_to:
                return False
        return True

    filtered_runs = [run for run in all_runs if _run_matches_filters(run)]
    st.caption(f"Найдено запусков: {len(filtered_runs)} из {len(all_runs)}")

    if not filtered_runs:
        st.info("Запусков, соответствующих фильтрам, не найдено.")

    for run in filtered_runs:
        parsed = run.get("parsed") or report_parser.empty_parsed_result()
        effective_parsed = report_parser.apply_manual_corrections(parsed)
        counts = report_parser.severity_counts(parsed)

        with st.container(border=True):
            header_cols = st.columns([3, 1, 1, 1])
            header_cols[0].markdown(f"**{run.get('project')} · {run.get('task_type')} · {run.get('agent')}**")
            header_cols[0].caption(f"{run.get('created_at', '—')} · repo: `{run.get('repository_path', '—')}`")
            header_cols[1].badge(
                models.RUN_STATUS_LABELS.get(run.get("status"), run.get("status")),
                color=models.RUN_STATUS_COLORS.get(run.get("status"), "gray"),
            )
            if effective_parsed.get("verdict"):
                header_cols[2].badge(
                    models.VERDICT_LABELS.get(effective_parsed["verdict"], effective_parsed["verdict"]), color="blue"
                )
            duration = run.get("duration_seconds")
            header_cols[3].caption(f"{duration:.1f}с" if isinstance(duration, (int, float)) else "—")

            if any(counts.values()):
                st.caption("Находки: " + " · ".join(f"{sev}: {counts[sev]}" for sev in models.SEVERITIES if counts[sev]))

            with st.expander("Детали запуска", icon=":material/info:"):
                st.write(f"Run ID: `{run['id']}`")
                st.write(f"Task ID: `{run.get('task_id') or '—'}`")
                pre_run = run.get("pre_run") or {}
                post_run = run.get("post_run") or {}
                st.write(f"Ветка до запуска: {pre_run.get('branch') or '—'} · после: {post_run.get('branch') or '—'}")
                st.write(f"HEAD до запуска: {pre_run.get('head') or '—'} · после: {post_run.get('head') or '—'}")
                st.write(f"Commit hash: {effective_parsed.get('commit_hash') or 'не указан'}")
                st.write(f"Recommended next action: {effective_parsed.get('recommended_next_action') or 'не указано'}")

                st.markdown("**Промпт:**")
                st.code(run.get("prompt", ""), language=None)

                stdout_text = run.get("stdout", "")
                st.markdown("**Stdout (предпросмотр в интерфейсе):**")
                st.code(stdout_text[:5000] or "—", language=None)
                if len(stdout_text) > 5000:
                    st.caption(
                        "Показаны первые 5000 символов вывода в интерфейсе — полный текст "
                        "сохранён в файле отчёта без сокращений."
                    )
                if run.get("stderr"):
                    st.markdown("**Stderr:**")
                    st.code(run["stderr"], language=None)

                if run.get("report_path"):
                    st.write(f"Отчёт: `{run['report_path']}`")
                    report_full_path = agent_runner.resolve_report_path(run)
                    if report_full_path is None:
                        st.warning("Путь к отчёту не проходит проверку безопасности — файл не открыт.")
                    elif report_full_path.exists():
                        with st.expander("Полный текст отчёта"):
                            st.markdown(read_text(report_full_path))

                if run.get("next_task_id"):
                    st.success(f"Следующая задача уже создана: `{run['next_task_id']}`")

                # Manual field correction write-back is a v1.2-journal feature
                # (it appends to runs.jsonl). v2 runs live in runtime.db and are
                # read-only here — persisting a correction would require a v2
                # correction store that does not exist yet, so we surface that
                # honestly rather than silently writing a stale v1.2 snapshot.
                if run.get("source") == "v1.2":
                    st.markdown("**Ручная корректировка полей**")
                    correction_cols = st.columns([1, 2, 1])
                    with correction_cols[0]:
                        correction_field = st.selectbox(
                            "Поле", report_parser.CORRECTABLE_FIELDS, key=f"run_correct_field_{run['id']}"
                        )
                    with correction_cols[1]:
                        correction_value = st.text_input("Значение", key=f"run_correct_value_{run['id']}")
                    with correction_cols[2]:
                        st.write("")
                        if st.button("Сохранить", key=f"run_correct_btn_{run['id']}"):
                            if correction_value.strip():
                                corrected_parsed = report_parser.set_manual_correction(
                                    parsed, correction_field, correction_value.strip()
                                )
                                run["parsed"] = corrected_parsed
                                agent_runner.append_run(run)
                                activity_log.log_event(
                                    "manual_field_correction", project=run.get("project"), task_id=run.get("task_id"),
                                    run_id=run["id"], message=f"{correction_field} -> {correction_value.strip()[:80]}",
                                )
                                st.success("Сохранено.")
                                st.rerun()
                else:
                    st.caption("Ручная корректировка полей доступна только для записей из журнала v1.2; этот прогон хранится в runtime.db и доступен только для чтения.")

            render_create_next_task_widget(run, tasks, key_prefix=f"runs_page_{run['id']}")


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

elif page_key == "timeline":
    st.subheader("Таймлайн")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + models.PROJECT_IDS, key="timeline_project_filter")

    events = build_timeline_events(
        tasks, runs=runs_read.list_unified_runs(get_execution_center_api().db_path, root=ROOT),
        activity_events=activity_log.load_activity(limit=200), limit=200,
    )
    if project_filter != "Все":
        # Canonical-id match (shared helper): task-sourced timeline events carry
        # the task's raw `project`, which may be a display name.
        events = [event for event in events if project_config.project_matches(event.get("project"), project_filter)]

    if not events:
        st.info("Событий пока нет.")
    else:
        current_date: str | None = None
        for event in events[:150]:
            event_date = datetime.fromtimestamp(event["ts"]).strftime("%d.%m.%Y")
            if event_date != current_date:
                current_date = event_date
                st.markdown(f"#### {current_date}")
            time_str = datetime.fromtimestamp(event["ts"]).strftime("%H:%M")
            project_tag = f" · {event['project']}" if event.get("project") else ""
            st.caption(f"{event['icon']} {time_str}{project_tag} — {event['label']}")


# --------------------------------------------------------------------------
# Project browser
# --------------------------------------------------------------------------

elif page_key == "projects":
    st.subheader("Проекты")

    project_choice = st.selectbox("Проект", ["Все", *models.PROJECT_IDS], key="project_browser_select")

    # "Все" is a cross-project overview; picking a project opens it *inside* the
    # project (status, tasks, reports, context, settings, chat as tabs) rather
    # than as separate sidebar pages. (task 02661825)
    if project_choice == "Все":
        st.caption(
            "Обзор всех проектов — выберите проект, чтобы открыть его внутри "
            "(задания, отчёты, контекст, настройки, чат)."
        )
        for overview_pid in models.PROJECT_IDS:
            pid_tasks = [t for t in tasks if project_config.project_matches(t.get("project"), overview_pid)]
            pid_active = [t for t in pid_tasks if t.get("status") != "Done"]
            pid_done = [t for t in pid_tasks if t.get("status") == "Done"]
            with st.container(border=True):
                st.markdown(f"**{project_configs[overview_pid]['display_name']}** (`{overview_pid}`)")
                st.caption(f"{len(pid_active)} активных · {len(pid_done)} завершено · всего {len(pid_tasks)}")
        st.stop()

    selected_project = project_choice
    project_file = project_status_file_path(selected_project)

    tab_status, tab_generated, tab_reports, tab_context, tab_settings, tab_chat, tab_audit, tab_roadmap = st.tabs(
        ["Статус", "Задания", "Отчёты", "Контекст", "Настройки", "Чат", "Аудит", "Roadmap"]
    )

    with tab_status:
        st.caption(f"Изменён: {format_mtime(project_file)}")
        st.markdown(read_text(project_file))

    with tab_generated:
        files = artifacts.list_markdown_files(GENERATED_DIR / selected_project)
        if not files:
            st.info("Для проекта пока нет сгенерированных задач.")
        else:
            chosen_name = st.selectbox("Файл задания", [path.name for path in files], key="proj_gen_select")
            chosen_path = next(path for path in files if path.name == chosen_name)
            st.caption(f"Изменён: {format_mtime(chosen_path)}")
            chosen_content = read_text(chosen_path)
            st.markdown(chosen_content)
            st.divider()
            render_agent_launcher(
                key_prefix=f"proj_gen_launch_{selected_project}_{chosen_name}",
                project=selected_project,
                default_prompt=chosen_content,
                tasks=tasks,
                default_task_type=artifacts.infer_task_type_from_filename(chosen_path) or "implementation",
            )

    with tab_reports:
        files = artifacts.list_markdown_files(REPORTS_DIR / selected_project)
        if not files:
            st.info("Для проекта пока нет отчётов.")
        else:
            chosen_name = st.selectbox("Файл отчёта", [path.name for path in files], key="proj_report_select")
            chosen_path = next(path for path in files if path.name == chosen_name)
            st.caption(f"Изменён: {format_mtime(chosen_path)}")
            st.markdown(read_text(chosen_path))

    with tab_context:
        context_name = CONTEXT_FILES.get(selected_project)
        if not context_name:
            st.info(f"Для проекта {selected_project} отдельный файл контекста ещё не создан.")
        else:
            context_path = CONTEXT_DIR / context_name
            if not context_path.exists():
                st.warning(f"Файл контекста не найден: context/{context_name}")
            else:
                st.caption(f"Изменён: {format_mtime(context_path)}")
                st.markdown(read_text(context_path))

    with tab_settings:
        cfg = project_configs[selected_project]
        st.write(f"Проект: **{cfg['display_name']}** (`{selected_project}`)")
        if cfg["sensitive"]:
            st.warning(
                "Проект помечен как чувствительный (BANK/LEGAL): файлы для агента не "
                "прикладываются автоматически, контекст добавляется вручную при запуске."
            )

        current_path = cfg.get("repository_path")
        if current_path:
            st.success(f"Текущий путь репозитория: `{current_path}`")
        else:
            st.info("Путь к репозиторию не настроен.")

        suggested_path = project_config.discover_candidate_repository_path(selected_project)
        if suggested_path and not current_path:
            st.info(
                f"Обнаружен вероятный путь репозитория (существующий git-репозиторий на "
                f"этой машине): `{suggested_path}`. Проверьте и сохраните, если это верно."
            )

        new_path_input = st.text_input(
            "Путь к репозиторию",
            value=current_path or suggested_path or "",
            key=f"repo_path_input_{selected_project}",
        )
        settings_cols = st.columns(2)
        with settings_cols[0]:
            if st.button("Сохранить путь", key=f"repo_path_save_{selected_project}", icon=":material/save:"):
                ok, message = project_config.validate_repository_path(new_path_input)
                if ok:
                    project_config.save_repository_path(selected_project, new_path_input.strip())
                    st.success("Путь сохранён.")
                    st.rerun()
                else:
                    st.error(message)
        with settings_cols[1]:
            if current_path and st.button(
                "Очистить путь", key=f"repo_path_clear_{selected_project}", icon=":material/delete:"
            ):
                project_config.save_repository_path(selected_project, None)
                st.success("Путь очищен.")
                st.rerun()

        st.caption(f"Разрешённые агенты: {', '.join(cfg['allowed_agents'])}")
        st.caption(f"Каталог отчётов: `{cfg['reports_dir']}` · Каталог заданий: `{cfg['generated_dir']}`")
        st.caption("Файлы контекста: " + (", ".join(f"`{p}`" for p in cfg["context_file_paths"]) or "—"))

        st.divider()
        st.markdown("#### Настройки проекта (по умолчанию для новых задач)")
        st.caption(
            "Эти значения автоматически наследуются новыми задачами проекта "
            "(workspace, branch, executor, prompt) на странице «Создать задачу»."
        )

        workspace_input = st.text_input(
            "Workspace по умолчанию",
            value=cfg.get("default_workspace_path") or "",
            key=f"default_workspace_input_{selected_project}",
        )
        branch_input = st.text_input(
            "Branch по умолчанию",
            value=cfg.get("default_branch") or "",
            key=f"default_branch_input_{selected_project}",
        )
        executor_options = ["(не задан)"] + executors.EXECUTOR_IDS
        current_executor = cfg.get("default_executor")
        executor_index = executor_options.index(current_executor) if current_executor in executor_options else 0
        executor_input = st.selectbox(
            "Executor по умолчанию",
            executor_options,
            index=executor_index,
            key=f"default_executor_input_{selected_project}",
        )
        prompt_input = st.text_area(
            "Prompt по умолчанию",
            value=cfg.get("default_prompt") or "",
            height=120,
            key=f"default_prompt_input_{selected_project}",
        )
        description_input = st.text_area(
            "Описание проекта",
            value=cfg.get("description") or "",
            height=80,
            key=f"description_input_{selected_project}",
        )

        meta_cols = st.columns(3)
        with meta_cols[0]:
            status_options = project_config.PROJECT_STATUSES
            current_status = cfg.get("status")
            status_index = status_options.index(current_status) if current_status in status_options else 0
            status_input = st.selectbox(
                "Статус проекта", status_options, index=status_index, key=f"status_input_{selected_project}"
            )
        with meta_cols[1]:
            priority_options = project_config.PROJECT_PRIORITIES
            current_priority = cfg.get("priority")
            priority_index = priority_options.index(current_priority) if current_priority in priority_options else 0
            priority_input = st.selectbox(
                "Приоритет проекта", priority_options, index=priority_index, key=f"priority_input_{selected_project}"
            )
        with meta_cols[2]:
            progress_input = st.number_input(
                "Прогресс (%)",
                min_value=0,
                max_value=100,
                value=int(cfg.get("progress") or 0),
                step=5,
                key=f"progress_input_{selected_project}",
            )

        owner_cols = st.columns(3)
        with owner_cols[0]:
            sprint_input = st.text_input(
                "Текущий спринт", value=cfg.get("current_sprint") or "", key=f"sprint_input_{selected_project}"
            )
        with owner_cols[1]:
            milestone_input = st.text_input(
                "Текущая веха", value=cfg.get("current_milestone") or "", key=f"milestone_input_{selected_project}"
            )
        with owner_cols[2]:
            owner_input = st.text_input(
                "Владелец проекта", value=cfg.get("owner") or "", key=f"owner_input_{selected_project}"
            )

        if st.button(
            "Сохранить настройки проекта", key=f"save_project_settings_{selected_project}", icon=":material/save:"
        ):
            candidate = dict(cfg)
            candidate.update(
                {
                    "default_workspace_path": workspace_input.strip() or None,
                    "default_branch": branch_input.strip() or None,
                    "default_executor": None if executor_input == "(не задан)" else executor_input,
                    "default_prompt": prompt_input.strip(),
                    "description": description_input.strip(),
                    "status": status_input,
                    "priority": priority_input,
                    "progress": int(progress_input),
                    "current_sprint": sprint_input.strip() or None,
                    "current_milestone": milestone_input.strip() or None,
                    "owner": owner_input.strip(),
                }
            )
            for warning_message in project_config.validate_project_settings(candidate):
                st.warning(warning_message)

            project_config.save_project_settings(
                selected_project,
                default_workspace_path=candidate["default_workspace_path"],
                default_branch=candidate["default_branch"],
                default_executor=candidate["default_executor"],
                default_prompt=candidate["default_prompt"],
                description=candidate["description"],
                status=candidate["status"],
                priority=candidate["priority"],
                progress=candidate["progress"],
                current_sprint=candidate["current_sprint"],
                current_milestone=candidate["current_milestone"],
                owner=candidate["owner"],
            )
            st.success("Настройки проекта сохранены.")
            st.rerun()

    with tab_chat:
        render_project_chat(selected_project, tasks, tasks_by_id)

    with tab_audit:
        st.caption(
            "Read-only аудит проекта (архитектура, правила, качество, UX). Результат "
            "превращается в предлагаемые задачи бэклога — примите нужные."
        )
        if st.button("Запустить аудит", key=f"proj_audit_run_{selected_project}", type="primary"):
            audit_task = create_task(
                selected_project,
                f"Аудит проекта {selected_project}: архитектура/правила/качество/UX",
                "architecture_review",
                "Next",
                goal="Провести read-only аудит проекта и предложить задачи для бэклога.",
                prompt=_project_audit_prompt(selected_project),
            )
            execution_queue.enqueue_and_persist(ROOT, audit_task, {**tasks_by_id, audit_task["id"]: audit_task})
            st.success(
                f"Аудит поставлен в очередь (задача {audit_task['id'][:8]}). Когда read-only "
                "агент завершит отчёт, его предложения появятся ниже."
            )

        report_text = _latest_audit_report_text(selected_project)
        if report_text:
            st.divider()
            candidates = backlog_proposals.parse_candidate_tasks(report_text)
            backlog_proposals.render_candidate_tasks(
                candidates,
                ROOT,
                selected_project,
                key_prefix=f"proj_audit_cand_{selected_project}",
                heading="Предложения из аудита",
            )
        else:
            st.caption("Отчётов аудита пока нет — запустите аудит выше.")

    with tab_roadmap:
        st.caption(
            "Переформатировать Roadmap проекта по новым пожеланиям: агент пересоберёт "
            "задачи/вехи/волны, покажет предпросмотр. Дубли уже сделанного отфильтровываются."
        )
        roadmap_wishes = st.text_area(
            "Пожелания к Roadmap",
            key=f"proj_roadmap_wishes_{selected_project}",
            placeholder="Например: сфокусироваться на надёжности пайплайна и качестве UX",
        )
        if st.button("Переформатировать Roadmap", key=f"proj_roadmap_run_{selected_project}", type="primary"):
            roadmap_task = create_task(
                selected_project,
                f"Переформатировать Roadmap: {selected_project}",
                "architecture_review",
                "Next",
                goal="Пересобрать roadmap/задачи/вехи проекта по новым пожеланиям.",
                prompt=_roadmap_reformat_prompt(selected_project, roadmap_wishes),
            )
            execution_queue.enqueue_and_persist(
                ROOT, roadmap_task, {**tasks_by_id, roadmap_task["id"]: roadmap_task}
            )
            st.success(
                f"Переформатирование поставлено в очередь (задача {roadmap_task['id'][:8]}). "
                "Когда агент завершит, новые задачи появятся ниже как предложения."
            )

        roadmap_report = _latest_roadmap_report_text(selected_project)
        if roadmap_report:
            st.divider()
            proposed = backlog_proposals.parse_candidate_tasks(roadmap_report)
            fresh = backlog_proposals.filter_new_candidates(proposed, tasks)
            if len(fresh) < len(proposed):
                st.caption(f"Отфильтровано дублей уже существующих задач: {len(proposed) - len(fresh)}.")
            backlog_proposals.render_candidate_tasks(
                fresh,
                ROOT,
                selected_project,
                key_prefix=f"proj_roadmap_cand_{selected_project}",
                heading="Новые задачи из обновлённого roadmap",
            )
        else:
            st.caption("Отчётов переформатирования пока нет — запустите выше.")


# --------------------------------------------------------------------------
# Generated tasks browser (global)
# --------------------------------------------------------------------------

elif page_key == "generated":
    st.subheader("Сгенерированные задачи")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + models.PROJECT_IDS, key="gen_filter")

    all_files = artifacts.list_markdown_files(GENERATED_DIR)
    filtered_files = (
        all_files
        if project_filter == "Все"
        else [path for path in all_files if artifacts.project_from_path(path, GENERATED_DIR) == project_filter]
    )

    if not filtered_files:
        st.info("Файлы заданий не найдены.")
    else:
        st.caption(f"Найдено файлов: {len(filtered_files)} (новые сверху)")
        for path in filtered_files:
            rel = path.relative_to(GENERATED_DIR)
            file_project = artifacts.project_from_path(path, GENERATED_DIR)
            with st.expander(f"{rel} · {format_mtime(path)}"):
                content = read_text(path)
                st.markdown(content)
                if file_project != "—":
                    st.divider()
                    render_agent_launcher(
                        key_prefix=f"gen_page_launch_{rel}".replace("/", "_"),
                        project=file_project,
                        default_prompt=content,
                        tasks=tasks,
                        default_task_type=artifacts.infer_task_type_from_filename(path) or "implementation",
                    )


# --------------------------------------------------------------------------
# Reports browser (global)
# --------------------------------------------------------------------------

elif page_key == "reports":
    st.subheader("Отчёты")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + models.PROJECT_IDS, key="report_filter")

    all_files = artifacts.list_markdown_files(REPORTS_DIR)
    filtered_files = (
        all_files
        if project_filter == "Все"
        else [path for path in all_files if artifacts.project_from_path(path, REPORTS_DIR) == project_filter]
    )

    if not filtered_files:
        st.info("Файлы отчётов не найдены.")
    else:
        st.caption(f"Найдено файлов: {len(filtered_files)} (новые сверху)")
        # Unified runs (v2 runtime.db + legacy v1.2) so a report file is joined
        # to its run regardless of which source produced it; the old
        # `agent_runner.load_runs()` only knew about v1.2 runs.
        runs_by_report_path = {
            run["report_path"]: run
            for run in runs_read.list_unified_runs(get_execution_center_api().db_path, root=ROOT)
            if run.get("report_path")
        }
        for path in filtered_files:
            rel = path.relative_to(REPORTS_DIR)
            matching_run = runs_by_report_path.get(f"reports/{rel}")
            with st.expander(f"{rel} · {format_mtime(path)}"):
                st.markdown(read_text(path))
                if matching_run:
                    st.divider()
                    parsed = report_parser.apply_manual_corrections(matching_run.get("parsed") or {})
                    st.markdown("**Извлечённые данные**")
                    st.write(f"Вердикт: {models.VERDICT_LABELS.get(parsed.get('verdict'), parsed.get('verdict') or 'не определён')}")
                    st.write(f"Уверенность парсера: {parsed.get('confidence', 'none')}")
                    counts = report_parser.severity_counts(matching_run.get("parsed"))
                    if any(counts.values()):
                        st.caption("Находки: " + " · ".join(f"{sev}: {counts[sev]}" for sev in models.SEVERITIES if counts[sev]))
                    render_create_next_task_widget(matching_run, tasks, key_prefix=f"reports_page_{matching_run['id']}")


# --------------------------------------------------------------------------
# Global context
# --------------------------------------------------------------------------

elif page_key == "context":
    st.subheader("Глобальный контекст")

    for name in GLOBAL_FILES:
        path = ROOT / name
        with st.expander(f"{name} · {format_mtime(path)}", expanded=(name == "CURRENT_STATE.md")):
            st.markdown(read_text(path))


# --------------------------------------------------------------------------
# Git Center
# --------------------------------------------------------------------------

elif page_key == "git_center":
    st.subheader("Git Center")

    # Multi-repo: the portfolio spans several configured repositories, not just
    # the app's own cwd. Surface every project's configured repository_path
    # (plus the app itself) so an operator can inspect any of them from one
    # place instead of only ever seeing AICC here.
    repos: list[tuple[str, Path]] = []
    if (ROOT / ".git").is_dir():
        repos.append(("AICC (app)", ROOT))
    for pid in models.PROJECT_IDS:
        cfg = project_configs.get(pid, {})
        repo_str = cfg.get("repository_path") or cfg.get("default_workspace_path")
        if not repo_str:
            continue
        repo_path = Path(repo_str).expanduser()
        if repo_path.is_dir() and repo_path not in [p for _, p in repos]:
            label = f"{cfg.get('display_name') or pid} ({pid})"
            repos.append((label, repo_path))

    if not repos:
        st.info("Не найдено ни одного настроенного git-репозитория.")
    else:
        # Per-repo summary table — one glance at the whole portfolio's git state.
        summary_rows = []
        for label, repo_path in repos:
            st_row = git_info.get_status(repo_path)
            if not st_row.get("is_repo"):
                summary_rows.append({"Проект": label, "Ветка": "—", "Статус": "не репозиторий",
                                     "Изменено": "—", "Неотслеж.": "—", "Коммит": "—"})
            else:
                summary_rows.append({
                    "Проект": label,
                    "Ветка": st_row.get("branch", "—"),
                    "Статус": "Изменения есть" if st_row.get("dirty") else "Чисто",
                    "Изменено": st_row.get("modified_count", 0),
                    "Неотслеж.": st_row.get("untracked_count", 0),
                    "Коммит": f"{st_row.get('last_commit_hash', '—')} {st_row.get('last_commit_subject', '')[:40]}",
                })
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)

        st.divider()
        repo_label = st.selectbox("Репозиторий для детального просмотра",
                                  [label for label, _ in repos], key="git_center_repo_select")
        repo_path = next(p for lbl, p in repos if lbl == repo_label)
        repo_status = git_info.get_status(repo_path)

        if not repo_status.get("is_repo"):
            st.info(f"«{repo_label}» не является git-репозиторием.")
        else:
            with st.container(horizontal=True):
                st.metric("Ветка", repo_status["branch"], border=True)
                st.metric("Статус", "Изменения есть" if repo_status["dirty"] else "Чисто", border=True)
                st.metric("Изменено файлов", repo_status["modified_count"], border=True)
                st.metric("Неотслеживаемых файлов", repo_status["untracked_count"], border=True)

            st.caption(f"Корень репозитория: `{repo_status['root']}`")
            st.caption(f"Последний коммит: `{repo_status['last_commit_hash']}` — {repo_status['last_commit_subject']}")

            tab_files, tab_log, tab_diff, tab_branches, tab_remotes = st.tabs(
                ["Изменённые файлы", "История коммитов", "Diff", "Ветки", "Remotes"]
            )

            with tab_files:
                status_lines = repo_status.get("status_lines", [])
                if not status_lines:
                    st.success("Нет изменений — рабочее дерево чистое.")
                else:
                    for line in status_lines:
                        st.caption(f"`{line[:2]}`  {line[3:]}")

            with tab_log:
                commits = git_info.get_log(repo_path, 20)
                if not commits:
                    st.info("История коммитов недоступна.")
                else:
                    for commit in commits:
                        with st.container(border=True):
                            st.markdown(f"**{commit['subject']}**")
                            st.caption(f"`{commit['hash']}` · {commit['author']} · {commit['date']}")

            with tab_diff:
                st.markdown("**Незафиксированные изменения (unstaged)**")
                st.code(git_info.get_diff_stat(repo_path, staged=False) or "Нет изменений.", language=None)
                st.markdown("**Подготовленные изменения (staged)**")
                st.code(git_info.get_diff_stat(repo_path, staged=True) or "Нет изменений.", language=None)

            with tab_branches:
                branches = git_info.get_branches(repo_path)
                if not branches:
                    st.info("Ветки не найдены.")
                else:
                    for branch in branches:
                        marker = "→ " if branch == repo_status["branch"] else "  "
                        st.caption(f"{marker}{branch}")

            with tab_remotes:
                remotes = git_info.get_remotes(repo_path)
                if not remotes:
                    st.info("Удалённые репозитории не настроены.")
                else:
                    for name, url in remotes:
                        st.caption(f"**{name}** — {url}")


# --------------------------------------------------------------------------
# Workspace Launcher
# --------------------------------------------------------------------------

elif page_key == "workspace":
    st.subheader("Workspace Launcher")
    st.caption("Быстрый переход к рабочим пространствам проектов и обзор git worktree.")

    st.markdown("#### Git worktrees")
    repo_status = get_git_status()
    if not repo_status.get("is_repo"):
        st.info("Текущая директория не является git-репозиторием.")
    else:
        worktrees = get_git_worktrees()
        if not worktrees:
            st.info("Информация о worktree недоступна.")
        else:
            for worktree in worktrees:
                with st.container(border=True):
                    st.markdown(f"**{worktree.get('branch', '—')}**")
                    st.caption(f"HEAD: `{worktree.get('head', '—')}`")
                    st.code(worktree.get("path", "—"), language=None)

    st.divider()
    st.markdown("#### Быстрый переход по проектам")

    for project in models.PROJECT_IDS:
        project_file = project_status_file_path(project)
        context_name = CONTEXT_FILES.get(project)
        project_active = sum(
            1
            for task in tasks
            if project_config.project_matches(task.get("project"), project) and task.get("status") != "Done"
        )
        project_generated = artifacts.list_markdown_files(GENERATED_DIR / project)
        last_activity = format_mtime(project_generated[0]) if project_generated else "—"

        with st.container(border=True):
            header_cols = st.columns([3, 1, 1])
            header_cols[0].markdown(f"**{project}**")
            header_cols[1].metric("Активные", project_active)
            header_cols[2].caption(f"Активность: {last_activity}")

            st.code(str(project_file), language=None)
            if context_name:
                st.code(str(CONTEXT_DIR / context_name), language=None)
            st.caption(f"generated/{project} · reports/{project}")

            btn_cols = st.columns(2)
            with btn_cols[0]:
                if st.button(
                    "Открыть проект",
                    key=f"launch_open_{project}",
                    icon=":material/folder_open:",
                    width="stretch",
                ):
                    st.session_state.pending_nav = "projects"
                    st.session_state.pending_project_browser = project
                    st.rerun()
            with btn_cols[1]:
                if st.button(
                    "Новая задача",
                    key=f"launch_new_{project}",
                    icon=":material/add_task:",
                    width="stretch",
                ):
                    st.session_state.pending_nav = "create"
                    st.session_state.pending_create_project = project
                    st.rerun()


# --------------------------------------------------------------------------
# Focus Mode
# --------------------------------------------------------------------------

elif page_key == "focus":
    if st.button("Выйти из Focus Mode", icon=":material/close:"):
        st.session_state.pending_nav = "dashboard"
        st.rerun()

    st.subheader("Focus Mode")

    active_tasks = [task for task in tasks if task.get("status") != "Done"]

    if not active_tasks:
        st.info("Нет активных задач для фокуса. Создайте задачу или откройте Kanban.")
    else:
        project_filter = st.selectbox("Проект", ["Все"] + models.PROJECT_IDS, key="focus_project_filter")
        candidates = [
            task
            for task in active_tasks
            if project_filter == "Все" or project_config.project_matches(task.get("project"), project_filter)
        ]

        if not candidates:
            st.info("Нет активных задач для выбранного проекта.")
        else:
            default_index = next(
                (i for i, task in enumerate(candidates) if task.get("status") == "In Progress"), 0
            )
            labels = [task_label(task) for task in candidates]
            chosen_index = st.selectbox(
                "Задача в фокусе",
                options=list(range(len(candidates))),
                format_func=lambda i: labels[i],
                index=default_index,
                key="focus_task_select",
            )
            task = candidates[chosen_index]
            task_id = task["id"]

            with st.container(border=True):
                st.markdown(f"## {task.get('title', 'Без названия')}")
                st.caption(f"{task.get('project')} · {TASK_TYPE_LABELS.get(task.get('task_type', ''), task.get('task_type'))}")

                task_progress = int(task.get("progress") or 0)
                task_stage = task.get("current_stage") or models.EXECUTION_STAGES[0]
                st.progress(task_progress / 100, text=f"{task_stage} — {task_progress}%")

                with st.container(horizontal=True):
                    priority = task.get("priority", "Medium")
                    st.badge(priority, color=PRIORITY_COLORS.get(priority, "blue"))
                    if task.get("owner"):
                        st.badge(task["owner"], color="gray", icon=":material/person:")
                    if task.get("estimate_hours"):
                        st.badge(format_estimate(task["estimate_hours"]), color="gray", icon=":material/schedule:")

                unmet = unmet_dependencies(task, tasks_by_id)
                if unmet:
                    names = ", ".join(
                        tasks_by_id[dep_id].get("title", "?")[:40] if dep_id in tasks_by_id else dep_id
                        for dep_id in unmet
                    )
                    st.warning(f"Заблокировано: {names}")

                st.divider()

                _focus_status = task.get("status", "Backlog")
                new_status = st.selectbox(
                    "Статус",
                    KANBAN_COLUMNS,
                    # Guard like the task card / Kanban do: a non-canonical status
                    # (e.g. "Blocked", which the board treats as live but is not a
                    # column) would make .index() raise ValueError and crash Focus
                    # Mode (audit M1). Fall back to the first column instead.
                    index=KANBAN_COLUMNS.index(_focus_status) if _focus_status in KANBAN_COLUMNS else 0,
                    key=f"focus_status_{task_id}",
                )
                if new_status != task.get("status"):
                    update_task_status(task_id, new_status)
                    st.rerun()

                if st.button(
                    "Отметить как выполнено",
                    icon=":material/check_circle:",
                    type="primary",
                    width="stretch",
                ):
                    update_task_status(task_id, "Done")
                    st.rerun()


# --------------------------------------------------------------------------
# Portfolio Execution
# --------------------------------------------------------------------------

elif page_key == "portfolio":
    st.subheader("Portfolio Execution")
    portfolio_panel.render_portfolio_execution_panel(
        root=ROOT,
        execution_center_api=get_execution_center_api(),
    )

elif page_key == "portfolio_overview":
    st.subheader("Portfolio Overview")
    portfolio_overview_panel.render_portfolio_overview_panel(root=ROOT)
