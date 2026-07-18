from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import streamlit as st

from command_center import (
    activity_log,
    agent_runner,
    artifacts,
    chat_service,
    executors,
    git_info,
    launch,
    launch_service,
    models,
    project_config,
    recommend,
    report_parser,
    storage,
    task_view,
    tasks_repository,
    workflow,
    workspace_home,
)
from command_center.runtime import api as runtime_api
from command_center.runtime import context_service as runtime_context_service
from command_center.runtime import db as runtime_db
from command_center.runtime import supervisor as runtime_supervisor

ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
GENERATED_DIR = ROOT / "generated"
REPORTS_DIR = ROOT / "reports"
CONTEXT_DIR = ROOT / "context"
DATA_DIR = storage.resolve_data_dir(ROOT)
TASKS_FILE = DATA_DIR / "tasks.json"
TASKS_EXAMPLE_FILE = DATA_DIR / "tasks.example.json"
START_TASK_SCRIPT = ROOT / "scripts" / "start-task.sh"

PROJECTS: dict[str, str] = {
    "AIOS": "AIOS.md",
    "BANK": "BANK_STRATEGY.md",
    "LEGAL": "LEGAL.md",
    "BUSINESS": "BUSINESS.md",
    "PERSONAL": "PERSONAL.md",
}

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

KANBAN_COLUMNS: list[str] = [
    "Backlog",
    "Next",
    "In Progress",
    "Review",
    "Done",
]

PRIORITIES: list[str] = ["Low", "Medium", "High", "Critical"]

PRIORITY_COLORS: dict[str, str] = {
    "Low": "gray",
    "Medium": "blue",
    "High": "orange",
    "Critical": "red",
}

LAUNCH_STATUS_COLORS: dict[str, str] = {
    "Ready": "gray",
    "Launching": "blue",
    "Running": "blue",
    "Completed": "green",
    "Failed": "red",
    "Requires Attention": "orange",
}

GLOBAL_FILES: list[str] = ["CURRENT_STATE.md", "DECISIONS.md", "INBOX.md"]

IGNORED_FILE_NAMES = {".DS_Store", ".gitkeep"}

NAV: dict[str, tuple[str, str]] = {
    "dashboard": ("Обзор", ":material/dashboard:"),
    "workspace_home": ("Workspace Home", ":material/home_work:"),
    "executive": ("Исполнительная панель", ":material/insights:"),
    "create": ("Создать задачу", ":material/add_task:"),
    "chat": ("Чат по проекту", ":material/forum:"),
    "kanban": ("Kanban", ":material/view_kanban:"),
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
                (key for key in PROJECTS if key.lower() in heading), None
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


def save_tasks(tasks: list[dict]) -> None:
    tasks_repository.save_tasks(ROOT, tasks)


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
    )


def update_task_status(tasks: list[dict], task_id: str, new_status: str) -> None:
    tasks_repository.update_task_status(ROOT, tasks, task_id, new_status)


def delete_task(tasks: list[dict], task_id: str) -> None:
    tasks_repository.delete_task(ROOT, tasks, task_id)


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

    if st.button("Запустить Claude Code", key=f"{key_prefix}_open_btn", icon=":material/smart_toy:"):
        st.session_state[confirm_key] = True

    if not st.session_state[confirm_key]:
        return

    with st.container(border=True):
        st.markdown("#### Подтверждение запуска агента")

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

        expected_branch = (task_for_launch or {}).get("branch")
        validation = launch.validate_launch(workspace_path=selection.path, expected_branch=expected_branch)
        actual_branch = (validation.git_status or {}).get("branch") if validation.git_status else None

        st.markdown("**Проверьте перед запуском:**")
        st.write(f"- Проект: `{project}`")
        st.write(f"- Репозиторий проекта: `{repo_path or '—'}`")
        st.write(f"- Выбранный workspace: `{selection.path}`")
        st.write(f"- Источник workspace: {launch.WORKSPACE_SOURCE_LABELS.get(selection.source, selection.source)}")
        st.write(f"- Ожидаемая ветка: `{expected_branch or '—'}`")
        st.write(f"- Текущая ветка: `{actual_branch or '—'}`")
        st.write("- Агент: `claude_code` (Claude Code CLI)")
        st.write(f"- Тип задачи: `{task_type}`")

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
                disabled=not confirmed or not validation.can_launch or not warnings_ack,
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
        if not validation.can_launch:
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

        with st.spinner("Выполняется Claude Code — это может занять несколько минут..."):
            outcome = launch_service.execute_agent_launch(
                project=project,
                task_type=task_type,
                prompt=full_prompt,
                timeout_seconds=int(timeout_seconds),
                repository_path=resolved_workspace,
                task=task_for_launch,
                executor_id="claude_code",
                validation=validation,
                on_task_state_changed=(lambda: save_tasks(tasks)) if task_for_launch is not None else None,
            )

        if task_for_launch is not None:
            save_tasks(tasks)

        st.session_state[confirm_key] = False

        if outcome.result_status == "completed":
            st.success(f"Запуск завершён. Вердикт: {outcome.parsed.get('verdict') or 'не определён'}.")
        elif outcome.result_status == "timed_out":
            st.error("Превышено время ожидания выполнения агента.")
        else:
            st.error(f"Агент завершился с ошибкой (exit code {outcome.run.get('exit_code')}).")
        st.info(f"Полный отчёт сохранён: `{outcome.report_relpath}`. Подробности — на странице «Журнал запусков».")


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
            new_task = new_task_record(
                project,
                models.derive_short_title(objective_clean),
                next_task_type,
                "Backlog",
                goal=objective_clean,
                parent_task_id=run.get("task_id"),
                prior_run_id=run["id"],
                workflow_stage=next_stage,
            )
            tasks.append(new_task)
            save_tasks(tasks)
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


def _set_launch_status(tasks: list[dict], task_id: str, status: str, note: str) -> None:
    tasks_repository.set_manual_launch_status(ROOT, tasks, task_id, status, note)


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

        with st.container(horizontal=True):
            priority = task.get("priority", "Medium")
            st.badge(priority, color=PRIORITY_COLORS.get(priority, "blue"))
            launch_status = task.get("launch_status", "Ready")
            st.badge(launch_status, color=LAUNCH_STATUS_COLORS.get(launch_status, "gray"))
            executor_id = task.get("executor")
            if executor_id:
                st.badge(executors.get_executor(executor_id).label, color="blue", icon=":material/smart_toy:")
            if task.get("branch"):
                st.badge(task["branch"], color="gray", icon=":material/fork_right:")
            if task.get("owner"):
                st.badge(task["owner"], color="gray", icon=":material/person:")
            if task.get("estimate_hours"):
                st.badge(format_estimate(task["estimate_hours"]), color="gray", icon=":material/schedule:")
            if models.is_blocked(task, tasks_by_id):
                st.badge("Заблокировано", color="red", icon=":material/block:")

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

            action_cols = st.columns(4)
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

            status_cols = st.columns(3)
            with status_cols[0]:
                if st.button("Pause", key=f"{key_prefix}_action_pause", icon=":material/pause:"):
                    _set_launch_status(tasks, task_id, "Requires Attention", "Отмечено как приостановлено (вручную).")
                    st.rerun()
            with status_cols[1]:
                if st.button("Resume", key=f"{key_prefix}_action_resume", icon=":material/play_arrow:"):
                    _set_launch_status(tasks, task_id, "Ready", "Отмечено как возобновлено (вручную).")
                    st.rerun()
            with status_cols[2]:
                if st.button("Restart", key=f"{key_prefix}_action_restart", icon=":material/restart_alt:"):
                    _set_launch_status(tasks, task_id, "Ready", "Отмечено для перезапуска (вручную).")
                    st.rerun()
            st.caption(
                "Pause/Resume/Restart — это статус-метки для планирования, а не управление процессом: "
                "синхронный запуск Claude Code нельзя приостановить на лету."
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
                update_task_status(tasks, task_id, new_status)
                st.rerun()

            if st.button("Удалить", key=f"{key_prefix}_delete", icon=":material/delete:", width="stretch"):
                delete_task(tasks, task_id)
                st.rerun()


def render_next_task_callout(tasks: list[dict], project: str | None = None) -> None:
    """Non-invasive '➡ Next Task' recommendation, always with an explanation
    of *why* — see `command_center.recommend.recommend_next_task`. Never
    creates, launches, or modifies anything; purely advisory."""
    recommendation = recommend.recommend_next_task(tasks, project=project)
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

# Non-terminal states — a run in one of these is still worth polling. Note the
# runtime only accepts cancellation while a run is actually RUNNING (see
# EXECUTION_CENTER_CANCELLABLE_STATES). The set itself lives in `runtime_db`
# (beside `TERMINAL_STATES`) so both `app.py` and Streamlit-free `command_center`
# modules (e.g. `workspace_home.py`) share the same source of truth.
EXECUTION_CENTER_ACTIVE_STATES: frozenset[str] = runtime_db.EXECUTION_CENTER_ACTIVE_STATES

# The Cancel action is only ever accepted by the runtime for a run that is
# actually RUNNING (PREPARED/QUEUED runs have no subprocess yet for the
# Supervisor to signal) — the UI must not offer a control implying otherwise.
EXECUTION_CENTER_CANCELLABLE_STATES: frozenset[str] = frozenset({"RUNNING"})


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
    """
    return runtime_api.ExecutionCenterAPI()


def _execution_center_event_line(event: dict) -> str:
    preview = json.dumps(event["payload"], ensure_ascii=False)[:500]
    return f"[{event['seq']:>5}] {event.get('created_at', '—')} {event['event_type']}: {preview}"


def _execution_center_poll_new_events(api: runtime_api.ExecutionCenterAPI, run_id: str) -> list[dict]:
    """Fetch only events after this run's last-seen cursor (both cursor and
    the accumulated list live in `st.session_state`, keyed per run id so
    switching runs never mixes logs), append them once, and return the full
    accumulated list. Never re-fetches or re-appends an event already
    stored — safe to call on every rerun without producing duplicate lines."""
    events_key = f"exec_center_events_{run_id}"
    cursor_key = f"exec_center_cursor_{run_id}"
    st.session_state.setdefault(events_key, [])
    st.session_state.setdefault(cursor_key, 0)
    new_events = api.get_events(run_id, after_seq=st.session_state[cursor_key])
    if new_events:
        st.session_state[events_key].extend(new_events)
        st.session_state[cursor_key] = new_events[-1]["seq"]
    return st.session_state[events_key]


def render_execution_center_launch_form(api: runtime_api.ExecutionCenterAPI) -> None:
    """Confirm-then-execute launcher for the v2 runtime, mirroring
    `render_agent_launcher`'s existing confirm/warn/disable pattern for
    BANK/LEGAL, but calling `ExecutionCenterAPI.start_run` (non-blocking:
    the Supervisor launches the subprocess and returns immediately with the
    run in state RUNNING) instead of `agent_runner.run_claude_code`
    (synchronous, blocks the whole script inside `st.spinner`)."""
    launch_project = st.selectbox("Проект", list(PROJECTS.keys()), key="exec_center_launch_project")
    cfg = project_config.get_project_config(launch_project)
    repo_path = cfg.get("repository_path")

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
        "Я подтверждаю запуск Claude Code с указанными параметрами.",
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

    ready = confirmed and sensitivity_ack and bool(instruction.strip())
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

    try:
        run = api.start_run(
            project=launch_project,
            repository_path=repo_path,
            task_type=task_type,
            instruction=instruction,
            confirmed=confirmed,
            timeout_seconds=int(timeout_seconds),
        )
    except (
        runtime_context_service.ConfirmationRequiredError,
        agent_runner.RunnerError,
        runtime_supervisor.SupervisorError,
    ) as exc:
        st.error(str(exc))
        return

    st.success(f"Запуск создан: `{run['id']}` (статус: {EXECUTION_CENTER_STATE_LABELS.get(run['state'], run['state'])})")
    st.session_state.pending_exec_center_run = run["id"]
    st.rerun()


def _render_execution_center_watch_body(
    api: runtime_api.ExecutionCenterAPI, run_id: str, run: dict
) -> None:
    """Live status/log/cancel panel body for one already-fetched `run` row.
    Shared by both the auto-polling and the one-shot render paths below, so
    the panel looks identical however it got drawn."""
    state = run.get("state", "UNKNOWN")
    st.markdown(f"#### Прогон `{run['id']}`")

    info_cols = st.columns(4)
    info_cols[0].metric("Статус", EXECUTION_CENTER_STATE_LABELS.get(state, state))
    info_cols[1].write(f"Проект: **{run.get('project', '—')}**")
    info_cols[2].write(f"Тип задачи: **{run.get('task_type', '—')}**")
    info_cols[3].write(f"Сессия: `{(run.get('session_id') or '—')[:8]}`")

    ts_cols = st.columns(3)
    ts_cols[0].caption(f"Создан: {run.get('created_at') or '—'}")
    ts_cols[1].caption(f"Начат: {run.get('started_at') or '—'}")
    ts_cols[2].caption(f"Завершён: {run.get('completed_at') or '—'}")

    if run.get("exit_code") is not None:
        st.caption(f"Код выхода: {run['exit_code']}")
    if run.get("failure_reason"):
        st.error(f"Причина ошибки: {run['failure_reason']}")
    if run.get("cancel_requested") and state in EXECUTION_CENTER_ACTIVE_STATES:
        st.info("Запрошена отмена — ожидание завершения процесса...")

    events = _execution_center_poll_new_events(api, run_id)
    st.markdown("**Журнал событий (инкрементально):**")
    with st.container(height=320, border=True):
        if events:
            st.code("\n".join(_execution_center_event_line(event) for event in events), language=None)
        else:
            st.caption("Событий пока нет.")

    result_events = [event for event in events if event["event_type"] == "result"]
    if result_events:
        st.markdown("**Итоговый результат:**")
        st.code(result_events[-1]["payload"].get("result") or "—", language=None)

    if state in EXECUTION_CENTER_CANCELLABLE_STATES:
        cancel_ack = st.checkbox(
            "Я подтверждаю отмену этого прогона.", key=f"exec_center_cancel_confirm_{run_id}"
        )
        cancel_clicked = st.button(
            "Отменить прогон",
            icon=":material/stop_circle:",
            key=f"exec_center_cancel_btn_{run_id}",
            disabled=not cancel_ack,
        )
        if cancel_clicked:
            if not cancel_ack:
                st.error("Отмена заблокирована: подтвердите отмену перед выполнением.")
            else:
                try:
                    api.request_cancel(run_id, confirmed=True)
                    st.success("Запрос на отмену отправлен.")
                except (runtime_supervisor.SupervisorError, KeyError) as exc:
                    st.error(str(exc))
                st.rerun()
    elif state in runtime_db.TERMINAL_STATES:
        st.caption("Прогон завершён — состояние сохранено в базе данных runtime.db.")


# Decisions for `_execution_center_watch_action` below. Kept as plain strings
# (not an Enum) — this is a private, in-module signal with exactly one
# consumer, not a value that crosses a boundary.
_EXECUTION_CENTER_WATCH_POLL_AND_RENDER = "poll_and_render"
_EXECUTION_CENTER_WATCH_FULL_RERUN = "full_rerun"
_EXECUTION_CENTER_WATCH_RENDER_MISSING = "render_missing"


def _execution_center_watch_action(run: dict | None) -> str:
    """Pure decision step for the polling fragment: given a freshly fetched
    `run` row (or `None` if the run no longer exists), decide what happens
    next. Deliberately has no Streamlit calls in it, so it — and the thin
    wrapper below that acts on it — can be unit-tested directly, without
    booting a Streamlit script context.

    - Still active: keep polling and render the live panel in place.
    - Reached a terminal state, or vanished entirely: a `run_every` fragment
      auto-rerun is a client-side JS `setInterval` that Streamlit only tears
      down on a *full* app rerun (see `streamlit/runtime/fragment.py` and
      the frontend's `handleAutoRerun`/`cleanupAutoReruns`) — a fragment-only
      rerun never reaches `render_execution_center_watch` to re-check this,
      so it would otherwise keep firing every 2s forever. Reporting anything
      other than "poll and render" tells the caller to request one full-app
      rerun instead of rendering through this path, which lands back in
      `render_execution_center_watch` and stops the polling for good.
    """
    if run is None:
        return _EXECUTION_CENTER_WATCH_RENDER_MISSING
    if run.get("state", "UNKNOWN") not in EXECUTION_CENTER_ACTIVE_STATES:
        return _EXECUTION_CENTER_WATCH_FULL_RERUN
    return _EXECUTION_CENTER_WATCH_POLL_AND_RENDER


def _execution_center_watch_poll_once(api: runtime_api.ExecutionCenterAPI, run_id: str) -> None:
    """One polling tick's worth of work, extracted out of the `@st.fragment`
    wrapper below so it can be called (and unit-tested) directly — a
    fragment-decorated function is a no-op outside of a live Streamlit script
    context, per `streamlit/runtime/fragment.py`.

    Never renders the terminal/missing-run panel itself: as soon as the
    freshly fetched state is no longer active (or the run vanished), it
    requests exactly one full-app rerun and returns, deferring to
    `render_execution_center_watch`'s non-polling branch for that render —
    that's what actually removes this fragment's `run_every` registration
    and stops the browser's polling timer.
    """
    run = api.get_run(run_id)
    if _execution_center_watch_action(run) != _EXECUTION_CENTER_WATCH_POLL_AND_RENDER:
        st.rerun()
        return
    _render_execution_center_watch_body(api, run_id, run)


@st.fragment(run_every=2.0)
def _render_execution_center_watch_polling(api: runtime_api.ExecutionCenterAPI, run_id: str) -> None:
    """Auto-refreshes itself every 2s (via `st.fragment(run_every=...)`,
    without blocking or re-running the rest of the page) while this fragment
    is on screen. Only ever dispatched to from `render_execution_center_watch`
    while the run is still active. See `_execution_center_watch_poll_once`
    for how it stops itself once the run stops being active."""
    _execution_center_watch_poll_once(api, run_id)


def render_execution_center_watch(api: runtime_api.ExecutionCenterAPI, run_id: str) -> None:
    """Dispatches to the auto-polling fragment above while the run is still
    active, and to a single, non-polling render once it reaches a terminal
    state (`runtime_db.TERMINAL_STATES`) or has been deleted.

    Every full-page rerun (switching the run selector, clicking Cancel,
    revisiting the page, or the one `st.rerun()` that
    `_execution_center_watch_poll_once` requests the moment it observes a
    terminal/missing run) re-evaluates this check and re-reads persisted
    truth fresh from `ExecutionCenterAPI`. Taking the non-polling branch
    here is what actually stops the `run_every` fragment's browser-side
    timer — it simply isn't called again this run, so Streamlit tears down
    its auto-rerun registration (see `_execution_center_watch_poll_once`'s
    docstring for why that's the only thing that works)."""
    run = api.get_run(run_id)
    if run is None:
        st.warning(f"Прогон `{run_id}` не найден.")
        return

    if run.get("state", "UNKNOWN") in EXECUTION_CENTER_ACTIVE_STATES:
        _render_execution_center_watch_polling(api, run_id)
    else:
        _render_execution_center_watch_body(api, run_id, run)


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
    "pending_exec_center_run": "exec_center_run_selector",
    "pending_exec_center_project": "exec_center_launch_project",
}
for _pending_key, _target_key in _PENDING_KEY_MAP.items():
    if _pending_key in st.session_state:
        st.session_state[_target_key] = st.session_state.pop(_pending_key)

st.set_page_config(
    page_title="AI Command Center",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed" if st.session_state.get("nav_page") == "focus" else "expanded",
)

st.title("🧭 AI Command Center")
st.caption("Единый центр управления проектами, задачами и AI-процессами")

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
        for project in PROJECTS
    )
    return commands


with st.sidebar:
    st.button(
        "Командная палитра (Mod+K)",
        icon=":material/search:",
        shortcut="Mod+K",
        on_click=_open_command_palette,
        width="stretch",
        key="open_palette_btn",
    )
    st.divider()
    st.markdown("### Навигация")
    page_key = st.radio(
        "Раздел",
        options=list(NAV.keys()),
        format_func=lambda key: f"{NAV[key][1]} {NAV[key][0]}",
        label_visibility="collapsed",
        key="nav_page",
    )
    st.divider()
    st.caption(f"Проектов в реестре: {len(PROJECTS)}")
    st.caption("Локальный режим · без внешних сервисов")

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


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

if page_key == "dashboard":
    st.subheader("Обзор")

    render_next_task_callout(tasks)

    active_tasks = [task for task in tasks if task.get("status") != "Done"]
    completed_tasks = [task for task in tasks if task.get("status") == "Done"]
    generated_count = len(artifacts.list_markdown_files(GENERATED_DIR))
    reports_count = len(artifacts.list_markdown_files(REPORTS_DIR))

    with st.container(horizontal=True):
        st.metric("Проекты", len(PROJECTS), border=True)
        st.metric("Активные задачи", len(active_tasks), border=True)
        st.metric("Завершённые задачи", len(completed_tasks), border=True)
        st.metric("Файлы заданий", generated_count, border=True)
        st.metric("Файлы отчётов", reports_count, border=True)

    st.divider()

    left, right = st.columns(2)

    with left:
        st.markdown("#### Активные задачи по проекту")
        for project in PROJECTS:
            project_active = [task for task in active_tasks if task.get("project") == project]
            with st.container(border=True):
                st.markdown(f"**{project}** · {len(project_active)}")
                if not project_active:
                    st.caption("Нет активных задач")
                else:
                    for task in project_active[:5]:
                        title = (task.get("title") or "Без названия")[:80]
                        st.caption(f"• {title} — {task.get('status')}")

    with right:
        st.markdown("#### Последняя активность")
        activity = gather_activity(12)
        if not activity:
            st.info("Активности пока нет.")
        else:
            with st.container(border=True):
                for path, mtime in activity:
                    rel = path.relative_to(ROOT)
                    stamp = datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")
                    st.caption(f"{stamp} — {rel}")


# --------------------------------------------------------------------------
# Workspace Home
# --------------------------------------------------------------------------

elif page_key == "workspace_home":
    st.subheader("Workspace Home")
    st.caption(
        "Кросс-проектная сводка: репозитории, прогоны, артефакты и отчёты — "
        "в одном месте, только для чтения."
    )
    render_workspace_home_page(get_execution_center_api())


# --------------------------------------------------------------------------
# Executive Dashboard
# --------------------------------------------------------------------------

elif page_key == "executive":
    st.subheader("Исполнительная панель")

    render_next_task_callout(tasks)

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
        for project in PROJECTS:
            project_tasks = [task for task in tasks if task.get("project") == project]
            p_active = sum(1 for task in project_tasks if task.get("status") != "Done")
            p_blocked = sum(1 for task in project_tasks if task["id"] in blocked_ids)
            p_done = sum(1 for task in project_tasks if task.get("status") == "Done")
            status_file = PROJECTS_DIR / PROJECTS[project]

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

    exec_runs = agent_runner.load_runs()
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

    with st.form("create_task_form"):
        title_input = st.text_input(
            "Название задачи",
            placeholder="Короткий заголовок, например: Исправить сортировку в Kanban",
            key="create_task_title",
        )
        project = st.selectbox("Проект", list(PROJECTS.keys()), key="create_task_project")
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
        )

    if submitted:
        title_clean = title_input.strip()
        objective_clean = objective.strip()

        if not title_clean:
            st.error("Укажите название задачи.")
        elif not objective_clean:
            st.error("Укажите цель задачи.")
        elif project not in PROJECTS:
            st.error("Неизвестный проект.")
        elif task_type not in TASK_TYPES:
            st.error("Неизвестный тип задачи.")
        else:
            with st.spinner("Выполняется scripts/start-task.sh..."):
                ok, stdout, stderr = run_start_task_script(project, task_type, objective_clean)

            if ok:
                tasks.append(
                    new_task_record(
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
                    )
                )
                save_tasks(tasks)
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


# --------------------------------------------------------------------------
# Project Chat
# --------------------------------------------------------------------------

elif page_key == "chat":
    st.subheader("Чат по проекту")

    conversations = chat_service.load_conversations()
    chat_project = st.selectbox("Проект", list(PROJECTS.keys()), key="chat_project_select")
    project_conversations = [c for c in conversations if c.get("project") == chat_project]
    chat_cfg = project_configs[chat_project]

    if chat_cfg["sensitive"]:
        st.warning(
            f"{chat_project} — чувствительный проект (BANK/LEGAL). Файлы не прикрепляются "
            "автоматически — добавляйте разрешённый контекст вручную."
        )

    conv_options = ["+ Новый разговор"] + [c["id"] for c in project_conversations]
    conv_labels = {c["id"]: f"{c.get('title', '—')} · {c.get('updated_at', '—')}" for c in project_conversations}
    chosen_conv_id = st.selectbox(
        "Разговор",
        conv_options,
        format_func=lambda value: "Новый разговор" if value == "+ Новый разговор" else conv_labels.get(value, value),
        key="chat_conv_select",
    )

    if chosen_conv_id == "+ Новый разговор":
        new_conv_title = st.text_input(
            "Название нового разговора", key="chat_new_title", placeholder="Например: обсуждение архитектуры P1"
        )
        project_task_options = ["Без привязки"] + [
            task["id"] for task in tasks if task.get("project") == chat_project
        ]
        link_task_id = st.selectbox(
            "Привязать к задаче (необязательно)",
            project_task_options,
            format_func=lambda value: "Без привязки" if value == "Без привязки" else task_label(tasks_by_id[value]),
            key="chat_link_task",
        )
        if st.button("Создать разговор", key="chat_create_conv_btn", icon=":material/add_comment:"):
            new_conv = models.new_conversation(
                chat_project,
                new_conv_title.strip() or "Новый разговор",
                task_id=None if link_task_id == "Без привязки" else link_task_id,
            )
            conversations.append(new_conv)
            chat_service.save_conversations(conversations)
            activity_log.log_event(
                "conversation_created", project=chat_project, task_id=new_conv.get("task_id"),
                conversation_id=new_conv["id"], message=new_conv["title"],
            )
            st.session_state.pending_chat_conv = new_conv["id"]
            st.rerun()
    else:
        active_conversation = chat_service.get_conversation(conversations, chosen_conv_id)
        if active_conversation is None:
            st.error("Разговор не найден.")
        else:
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
                                    new_task_from_msg = new_task_record(
                                        active_conversation["project"],
                                        models.derive_short_title(objective_clean),
                                        conv_task_type,
                                        "Backlog",
                                        goal=objective_clean,
                                    )
                                    tasks.append(new_task_from_msg)
                                    save_tasks(tasks)
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
                    context_text = _build_project_context_text(chat_project) if include_context else ""
                    updated_conversation = chat_service.get_conversation(conversations, active_conversation["id"])
                    try:
                        with st.spinner("Ожидание ответа провайдера..."):
                            response_text = chat_service.get_provider(chosen_provider_name).send(
                                messages=updated_conversation["messages"],
                                project_context=context_text,
                                project_id=chat_project,
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
                project=chat_project,
                default_prompt=last_user_message,
                tasks=tasks,
                task_id=active_conversation.get("task_id"),
            )


# --------------------------------------------------------------------------
# Kanban board
# --------------------------------------------------------------------------

elif page_key == "kanban":
    st.subheader("Kanban")

    filter_cols = st.columns([1, 2])
    with filter_cols[0]:
        project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()))
    with filter_cols[1]:
        priority_filter = st.multiselect("Приоритет", PRIORITIES, default=PRIORITIES, key="kanban_priority_filter")

    filtered_tasks = [
        task
        for task in tasks
        if (project_filter == "Все" or task.get("project") == project_filter)
        and task.get("priority", "Medium") in priority_filter
    ]

    columns = st.columns(len(KANBAN_COLUMNS))
    kanban_git_status_cache: dict[str, dict] = {}

    for column, status in zip(columns, KANBAN_COLUMNS, strict=True):
        with column:
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
                    "Проект", list(PROJECTS.keys()), key=f"agent_launch_project_{task_type}"
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
        "Запуск и наблюдение за прогонами Claude Code через v2 Session Supervisor "
        "(command_center.runtime) — отдельно от синхронного запуска на странице «AI-агенты»."
    )

    execution_center_api = get_execution_center_api()
    recent_runs = execution_center_api.list_runs(limit=20)

    with st.expander(
        "Запустить новый прогон", icon=":material/smart_toy:", expanded=not recent_runs
    ):
        render_execution_center_launch_form(execution_center_api)

    st.divider()

    if not recent_runs:
        st.info("Прогонов пока нет — запустите первый выше.")
    else:
        run_options = [run["id"] for run in recent_runs]
        runs_by_id = {run["id"]: run for run in recent_runs}

        def _format_execution_center_run_option(run_id: str) -> str:
            match = runs_by_id.get(run_id)
            if match is None:
                return run_id
            state_label = EXECUTION_CENTER_STATE_LABELS.get(match["state"], match["state"])
            return (
                f"{match['id'][:8]} · {match['project']} · {match['task_type']} · "
                f"{state_label} · {match.get('created_at', '—')}"
            )

        selected_run_id = st.selectbox(
            "Прогон",
            run_options,
            format_func=_format_execution_center_run_option,
            key="exec_center_run_selector",
        )
        render_execution_center_watch(execution_center_api, selected_run_id)


# --------------------------------------------------------------------------
# Run journal
# --------------------------------------------------------------------------

elif page_key == "runs":
    st.subheader("Журнал запусков")

    all_runs = agent_runner.load_runs()

    filter_cols = st.columns(4)
    with filter_cols[0]:
        runs_project_filter = st.selectbox("Проект", ["Все"] + list(PROJECTS.keys()), key="runs_project_filter")
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

            render_create_next_task_widget(run, tasks, key_prefix=f"runs_page_{run['id']}")


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

elif page_key == "timeline":
    st.subheader("Таймлайн")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()), key="timeline_project_filter")

    events = build_timeline_events(
        tasks, runs=agent_runner.load_runs(), activity_events=activity_log.load_activity(limit=200), limit=200
    )
    if project_filter != "Все":
        events = [event for event in events if event.get("project") == project_filter]

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

    selected_project = st.selectbox("Проект", list(PROJECTS.keys()), key="project_browser_select")
    project_file = PROJECTS_DIR / PROJECTS[selected_project]

    tab_status, tab_generated, tab_reports, tab_context, tab_settings = st.tabs(
        ["Статус проекта", "Сгенерированные задачи", "Отчёты", "Контекст", "Настройки репозитория"]
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


# --------------------------------------------------------------------------
# Generated tasks browser (global)
# --------------------------------------------------------------------------

elif page_key == "generated":
    st.subheader("Сгенерированные задачи")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()), key="gen_filter")

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

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()), key="report_filter")

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
        runs_by_report_path = {
            run["report_path"]: run for run in agent_runner.load_runs() if run.get("report_path")
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

    repo_status = get_git_status()

    if not repo_status.get("is_repo"):
        st.info("Текущая директория не является git-репозиторием.")
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
            commits = get_git_log(20)
            if not commits:
                st.info("История коммитов недоступна.")
            else:
                for commit in commits:
                    with st.container(border=True):
                        st.markdown(f"**{commit['subject']}**")
                        st.caption(f"`{commit['hash']}` · {commit['author']} · {commit['date']}")

        with tab_diff:
            st.markdown("**Незафиксированные изменения (unstaged)**")
            st.code(get_git_diff_stat(staged=False) or "Нет изменений.", language=None)
            st.markdown("**Подготовленные изменения (staged)**")
            st.code(get_git_diff_stat(staged=True) or "Нет изменений.", language=None)

        with tab_branches:
            branches = get_git_branches()
            if not branches:
                st.info("Ветки не найдены.")
            else:
                for branch in branches:
                    marker = "→ " if branch == repo_status["branch"] else "  "
                    st.caption(f"{marker}{branch}")

        with tab_remotes:
            remotes = get_git_remotes()
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

    for project in PROJECTS:
        project_file = PROJECTS_DIR / PROJECTS[project]
        context_name = CONTEXT_FILES.get(project)
        project_active = sum(1 for task in tasks if task.get("project") == project and task.get("status") != "Done")
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
        project_filter = st.selectbox("Проект", ["Все"] + list(PROJECTS.keys()), key="focus_project_filter")
        candidates = [
            task
            for task in active_tasks
            if project_filter == "Все" or task.get("project") == project_filter
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

                new_status = st.selectbox(
                    "Статус",
                    KANBAN_COLUMNS,
                    index=KANBAN_COLUMNS.index(task.get("status", "Backlog")),
                    key=f"focus_status_{task_id}",
                )
                if new_status != task.get("status"):
                    update_task_status(tasks, task_id, new_status)
                    st.rerun()

                if st.button(
                    "Отметить как выполнено",
                    icon=":material/check_circle:",
                    type="primary",
                    width="stretch",
                ):
                    update_task_status(tasks, task_id, "Done")
                    st.rerun()
