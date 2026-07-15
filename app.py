from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
GENERATED_DIR = ROOT / "generated"
REPORTS_DIR = ROOT / "reports"
CONTEXT_DIR = ROOT / "context"
DATA_DIR = ROOT / "data"
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

TASK_TYPES: list[str] = [
    "implementation",
    "review",
    "remediation",
    "final_gate",
    "architecture_review",
]

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

GLOBAL_FILES: list[str] = ["CURRENT_STATE.md", "DECISIONS.md", "INBOX.md"]

IGNORED_FILE_NAMES = {".DS_Store", ".gitkeep"}

NAV: dict[str, tuple[str, str]] = {
    "dashboard": ("Обзор", ":material/dashboard:"),
    "executive": ("Исполнительная панель", ":material/insights:"),
    "create": ("Создать задачу", ":material/add_task:"),
    "kanban": ("Kanban", ":material/view_kanban:"),
    "agents": ("AI-агенты", ":material/smart_toy:"),
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


def infer_task_type_from_filename(path: Path) -> str | None:
    parts = path.stem.split("_", 1)
    if len(parts) == 2 and parts[1] in TASK_TYPES:
        return parts[1]
    return None


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


def normalize_task(task: dict) -> dict:
    task.setdefault("priority", "Medium")
    task.setdefault("owner", "")
    task.setdefault("estimate_hours", 0.0)
    task.setdefault("depends_on", [])
    task.setdefault("updated_at", task.get("created_at", ""))
    return task


def load_tasks() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TASKS_FILE.exists():
        if TASKS_EXAMPLE_FILE.exists():
            shutil.copyfile(TASKS_EXAMPLE_FILE, TASKS_FILE)
        else:
            save_tasks([])
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [normalize_task(task) for task in data]


def save_tasks(tasks: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=DATA_DIR, prefix=".tasks_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tasks, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, TASKS_FILE)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def new_task_record(
    project: str,
    title: str,
    task_type: str,
    status: str,
    priority: str = "Medium",
    owner: str = "",
    estimate_hours: float = 0.0,
    depends_on: list[str] | None = None,
) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": uuid.uuid4().hex,
        "project": project,
        "title": title,
        "task_type": task_type,
        "status": status,
        "priority": priority,
        "owner": owner,
        "estimate_hours": estimate_hours,
        "depends_on": depends_on or [],
        "created_at": now,
        "updated_at": now,
    }


def update_task_status(tasks: list[dict], task_id: str, new_status: str) -> None:
    for task in tasks:
        if task.get("id") == task_id:
            task["status"] = new_status
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
            break
    save_tasks(tasks)


def delete_task(tasks: list[dict], task_id: str) -> None:
    remaining = [task for task in tasks if task.get("id") != task_id]
    save_tasks(remaining)


def task_label(task: dict) -> str:
    title = (task.get("title") or "—")[:50]
    return f"[{task.get('project')}] {title} · {task.get('status')}"


def unmet_dependencies(task: dict, tasks_by_id: dict[str, dict]) -> list[str]:
    return [
        dep_id
        for dep_id in task.get("depends_on", [])
        if tasks_by_id.get(dep_id, {}).get("status") != "Done"
    ]


def is_blocked(task: dict, tasks_by_id: dict[str, dict]) -> bool:
    return bool(unmet_dependencies(task, tasks_by_id))


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
# Git (read-only)
# --------------------------------------------------------------------------


def run_git_command(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def get_git_status() -> dict[str, object]:
    toplevel = run_git_command(["rev-parse", "--show-toplevel"])
    if toplevel is None or toplevel.returncode != 0:
        return {"is_repo": False}

    branch = run_git_command(["branch", "--show-current"])
    status = run_git_command(["status", "--porcelain"])
    head_hash = run_git_command(["rev-parse", "--short", "HEAD"])
    head_subject = run_git_command(["log", "-1", "--pretty=%s"])

    status_lines = [
        line
        for line in (status.stdout.splitlines() if status and status.returncode == 0 else [])
        if line
    ]
    untracked_count = sum(1 for line in status_lines if line.startswith("??"))
    modified_count = len(status_lines) - untracked_count

    return {
        "is_repo": True,
        "root": toplevel.stdout.strip(),
        "branch": branch.stdout.strip() if branch and branch.stdout.strip() else "(detached HEAD)",
        "dirty": bool(status_lines),
        "modified_count": modified_count,
        "untracked_count": untracked_count,
        "last_commit_hash": head_hash.stdout.strip() if head_hash and head_hash.returncode == 0 else "—",
        "last_commit_subject": head_subject.stdout.strip() if head_subject and head_subject.returncode == 0 else "—",
        "status_lines": status_lines,
    }


def get_git_log(limit: int = 20) -> list[dict[str, str]]:
    result = run_git_command(
        ["log", f"-{limit}", "--pretty=format:%h%x1f%an%x1f%ad%x1f%s", "--date=short"],
        timeout=10,
    )
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return []

    commits: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return commits


def get_git_diff_stat(staged: bool = False) -> str:
    args = ["diff", "--cached", "--stat"] if staged else ["diff", "--stat"]
    result = run_git_command(args, timeout=10)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_git_branches() -> list[str]:
    result = run_git_command(["branch", "--list", "--format=%(refname:short)"])
    if result is None or result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_git_remotes() -> list[tuple[str, str]]:
    result = run_git_command(["remote", "-v"])
    if result is None or result.returncode != 0:
        return []
    seen: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return list(seen.items())


def get_git_worktrees() -> list[dict[str, str]]:
    result = run_git_command(["worktree", "list", "--porcelain"], timeout=10)
    if result is None or result.returncode != 0:
        return []

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()[:10]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip().removeprefix("refs/heads/")
        elif line == "bare":
            current["branch"] = "(bare)"
        elif line == "detached":
            current["branch"] = "(detached)"

    if current:
        worktrees.append(current)

    return worktrees


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------


def build_timeline_events(tasks: list[dict], limit: int = 200) -> list[dict]:
    events: list[dict] = []

    for task in tasks:
        created = task.get("created_at")
        created_ts: float | None = None
        if created:
            try:
                created_ts = datetime.fromisoformat(created).timestamp()
            except ValueError:
                created_ts = None
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
            try:
                updated_ts = datetime.fromisoformat(updated).timestamp()
            except ValueError:
                updated_ts = None
            if updated_ts is not None:
                events.append(
                    {
                        "ts": updated_ts,
                        "icon": ":material/sync_alt:",
                        "label": f"Статус «{task.get('status')}»: {(task.get('title') or '')[:80]}",
                        "project": task.get("project"),
                    }
                )

    for path, mtime in gather_activity(limit=limit):
        project = None
        for base in (GENERATED_DIR, REPORTS_DIR):
            if base in path.parents:
                candidate = project_from_path(path, base)
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

    active_tasks = [task for task in tasks if task.get("status") != "Done"]
    completed_tasks = [task for task in tasks if task.get("status") == "Done"]
    generated_count = len(list_markdown_files(GENERATED_DIR))
    reports_count = len(list_markdown_files(REPORTS_DIR))

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
# Executive Dashboard
# --------------------------------------------------------------------------

elif page_key == "executive":
    st.subheader("Исполнительная панель")

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


# --------------------------------------------------------------------------
# Task creator
# --------------------------------------------------------------------------

elif page_key == "create":
    st.subheader("Создание AI-задачи")

    open_tasks = [task for task in tasks if task.get("status") != "Done"]

    with st.form("create_task_form"):
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
        objective_clean = objective.strip()

        if not objective_clean:
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
                        objective_clean,
                        task_type,
                        initial_status,
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

    for column, status in zip(columns, KANBAN_COLUMNS, strict=True):
        with column:
            status_tasks = [task for task in filtered_tasks if task.get("status") == status]
            st.markdown(f"**{status}**")
            st.caption(f"{len(status_tasks)} задач")

            if not status_tasks:
                st.caption("Пусто")

            for task in status_tasks:
                task_id = task.get("id")
                with st.container(border=True):
                    title = task.get("title") or "Без названия"
                    st.markdown(f"**{title[:60]}**")
                    st.caption(f"{task.get('project')} · {task.get('task_type')}")

                    with st.container(horizontal=True):
                        priority = task.get("priority", "Medium")
                        st.badge(priority, color=PRIORITY_COLORS.get(priority, "blue"))
                        if task.get("owner"):
                            st.badge(task["owner"], color="gray", icon=":material/person:")
                        if task.get("estimate_hours"):
                            st.badge(format_estimate(task["estimate_hours"]), color="gray", icon=":material/schedule:")
                        if is_blocked(task, tasks_by_id):
                            st.badge("Заблокировано", color="red", icon=":material/block:")

                    with st.expander("Детали", icon=":material/info:"):
                        st.write(f"ID: `{task_id}`")
                        st.write(f"Создано: {task.get('created_at', '—')}")
                        st.write(f"Обновлено: {task.get('updated_at', '—')}")
                        st.write("Полный текст:")
                        st.write(title)
                        deps = task.get("depends_on", [])
                        if deps:
                            st.write("Зависимости:")
                            for dep_id in deps:
                                dep = tasks_by_id.get(dep_id)
                                label = f"{dep.get('title', '')[:50]} ({dep.get('status')})" if dep else f"(удалена) {dep_id}"
                                st.caption(f"- {label}")

                    new_status = st.selectbox(
                        "Статус",
                        KANBAN_COLUMNS,
                        index=KANBAN_COLUMNS.index(status),
                        key=f"status_select_{task_id}",
                        label_visibility="collapsed",
                    )

                    if new_status != status:
                        update_task_status(tasks, task_id, new_status)
                        st.rerun()

                    if st.button(
                        "Удалить",
                        key=f"delete_{task_id}",
                        icon=":material/delete:",
                        width="stretch",
                    ):
                        delete_task(tasks, task_id)
                        st.rerun()


# --------------------------------------------------------------------------
# AI Agents
# --------------------------------------------------------------------------

elif page_key == "agents":
    st.subheader("AI-агенты")
    st.caption("Каталог типов задач, поддерживаемых scripts/start-task.sh")

    generated_files = list_markdown_files(GENERATED_DIR)

    for task_type in TASK_TYPES:
        meta = AGENT_ROLES[task_type]
        type_tasks = [task for task in tasks if task.get("task_type") == task_type]
        active_count = sum(1 for task in type_tasks if task.get("status") != "Done")
        done_count = len(type_tasks) - active_count
        generated_count = sum(1 for path in generated_files if infer_task_type_from_filename(path) == task_type)

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


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

elif page_key == "timeline":
    st.subheader("Таймлайн")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()), key="timeline_project_filter")

    events = build_timeline_events(tasks, limit=200)
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

    tab_status, tab_generated, tab_reports, tab_context = st.tabs(
        ["Статус проекта", "Сгенерированные задачи", "Отчёты", "Контекст"]
    )

    with tab_status:
        st.caption(f"Изменён: {format_mtime(project_file)}")
        st.markdown(read_text(project_file))

    with tab_generated:
        files = list_markdown_files(GENERATED_DIR / selected_project)
        if not files:
            st.info("Для проекта пока нет сгенерированных задач.")
        else:
            chosen_name = st.selectbox("Файл задания", [path.name for path in files], key="proj_gen_select")
            chosen_path = next(path for path in files if path.name == chosen_name)
            st.caption(f"Изменён: {format_mtime(chosen_path)}")
            st.markdown(read_text(chosen_path))

    with tab_reports:
        files = list_markdown_files(REPORTS_DIR / selected_project)
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


# --------------------------------------------------------------------------
# Generated tasks browser (global)
# --------------------------------------------------------------------------

elif page_key == "generated":
    st.subheader("Сгенерированные задачи")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()), key="gen_filter")

    all_files = list_markdown_files(GENERATED_DIR)
    filtered_files = (
        all_files
        if project_filter == "Все"
        else [path for path in all_files if project_from_path(path, GENERATED_DIR) == project_filter]
    )

    if not filtered_files:
        st.info("Файлы заданий не найдены.")
    else:
        st.caption(f"Найдено файлов: {len(filtered_files)} (новые сверху)")
        for path in filtered_files:
            rel = path.relative_to(GENERATED_DIR)
            with st.expander(f"{rel} · {format_mtime(path)}"):
                st.markdown(read_text(path))


# --------------------------------------------------------------------------
# Reports browser (global)
# --------------------------------------------------------------------------

elif page_key == "reports":
    st.subheader("Отчёты")

    project_filter = st.selectbox("Фильтр по проекту", ["Все"] + list(PROJECTS.keys()), key="report_filter")

    all_files = list_markdown_files(REPORTS_DIR)
    filtered_files = (
        all_files
        if project_filter == "Все"
        else [path for path in all_files if project_from_path(path, REPORTS_DIR) == project_filter]
    )

    if not filtered_files:
        st.info("Файлы отчётов не найдены.")
    else:
        st.caption(f"Найдено файлов: {len(filtered_files)} (новые сверху)")
        for path in filtered_files:
            rel = path.relative_to(REPORTS_DIR)
            with st.expander(f"{rel} · {format_mtime(path)}"):
                st.markdown(read_text(path))


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

    git_info = get_git_status()

    if not git_info.get("is_repo"):
        st.info("Текущая директория не является git-репозиторием.")
    else:
        with st.container(horizontal=True):
            st.metric("Ветка", git_info["branch"], border=True)
            st.metric("Статус", "Изменения есть" if git_info["dirty"] else "Чисто", border=True)
            st.metric("Изменено файлов", git_info["modified_count"], border=True)
            st.metric("Неотслеживаемых файлов", git_info["untracked_count"], border=True)

        st.caption(f"Корень репозитория: `{git_info['root']}`")
        st.caption(f"Последний коммит: `{git_info['last_commit_hash']}` — {git_info['last_commit_subject']}")

        tab_files, tab_log, tab_diff, tab_branches, tab_remotes = st.tabs(
            ["Изменённые файлы", "История коммитов", "Diff", "Ветки", "Remotes"]
        )

        with tab_files:
            status_lines = git_info.get("status_lines", [])
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
                    marker = "→ " if branch == git_info["branch"] else "  "
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
    git_info = get_git_status()
    if not git_info.get("is_repo"):
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
        project_generated = list_markdown_files(GENERATED_DIR / project)
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
