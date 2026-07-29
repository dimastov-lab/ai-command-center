"""Russian UI strings — the single source of truth for user-visible text.

The desktop application is Russian-only for its operator, so translations live
here as one registry rather than as per-locale ``.qm`` files. Keeping every
user-facing string in this one module is the i18n seam: a later move to
``QTranslator``/multi-locale would change only this module and the accessors,
not the call sites. Widget *identifiers* (``objectName``) are not translated —
they stay stable English keys.

The automated gate ``tests/desktop/test_ui_language_russian.py`` walks the live
widget tree and fails on any user-visible Latin string that is not an allowed
proper noun / technical abbreviation, so a newly-added English string is caught.
"""

from __future__ import annotations

# Product name — a proper noun, intentionally not translated.
APP_TITLE = "AI Command Center"

# --- Sidebar / navigation -------------------------------------------------
NAV_HEADER = "РАЗДЕЛЫ"
NAV_ACCESSIBLE = "Основная навигация"
DISABLED_TOOLTIP = "Появится в следующем выпуске"

# Section labels keyed by the stable section key (`sections.py`). "Git" stays a
# proper noun.
SECTION_LABELS: dict[str, str] = {
    "home": "Главная",
    "projects": "Проекты",
    "sessions": "Сессии",
    "execution": "Выполнение",
    "git": "Git",
    "artifacts": "Артефакты",
    "reports": "Отчёты",
    "agents": "Агенты",
    "settings": "Настройки",
}

# --- Top bar --------------------------------------------------------------
PROJECT_SWITCHER_PLACEHOLDER = "Выберите проект"
PROJECT_SWITCHER_ACCESSIBLE = "Переключатель проектов"
PROJECT_SWITCHER_DESCRIPTION = "Выбор проекта появится в следующем инкременте"
STATUS_AREA_ACCESSIBLE = "Область статуса"
REFRESH_TEXT = "Обновить"
REFRESH_TOOLTIP = "Обновить текущую страницу"

# --- Home page ------------------------------------------------------------
HOME_TITLE = "Главная"
HOME_SUBTITLE = "Сводка по всем проектам: проекты, запуски и активность."
HOME_EMPTY_TITLE = "Рабочий стол ещё не подключён к данным"
HOME_EMPTY_BODY = (
    "Здесь появится сводка по всем проектам — проекты, активные запуски, "
    "недавняя активность, артефакты и отчёты. Подключение к «живым» данным "
    "появится в следующем инкременте. Пока настройте проект, чтобы начать."
)
HOME_EMPTY_ACTION = "Перейти к проектам"

# --- Projects page --------------------------------------------------------
PROJECTS_TITLE = "Проекты"
PROJECTS_SUBTITLE = "Статус репозитория, worktree и настройка по каждому проекту."
PROJECTS_EMPTY_TITLE = "Настройка проектов ещё не подключена"
PROJECTS_EMPTY_BODY = (
    "Здесь появится просмотр и изменение пути к репозиторию каждого проекта, "
    "а также состояние его worktree и репозитория. Раздел активируется в "
    "следующем инкременте на основе существующего сервиса настройки проектов."
)

# --- Settings page --------------------------------------------------------
SETTINGS_TITLE = "Настройки"
SETTINGS_SUBTITLE = "Внешний вид, окно и параметры рабочего пространства."
SETTINGS_APPEARANCE_GROUP = "Внешний вид"
SETTINGS_APPEARANCE_ACCESSIBLE = "Настройки внешнего вида"
THEME_LIGHT = "Светлая"
THEME_DARK = "Тёмная"
THEME_SYSTEM = "Системная (как в macOS)"
SETTINGS_MORE_TITLE = "Скоро появятся дополнительные настройки"
SETTINGS_MORE_BODY = (
    "Плотность интерфейса, сброс геометрии окна и параметры рабочего "
    "пространства появятся здесь в следующем инкременте."
)


def page_accessible_name(title: str) -> str:
    """Accessible name for a page given its (already-Russian) visible title."""
    return f"Страница «{title}»"


def theme_accessible_name(label: str) -> str:
    """Accessible name for a theme radio given its (already-Russian) label."""
    return f"Тема: {label}"
