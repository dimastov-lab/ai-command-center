from command_center.desktop.pages.operational import OperationalPage
from command_center.desktop.sections import ACTIVE_SECTION_KEYS, SECTIONS


OPERATIONAL_KEYS = ("sessions", "execution", "git", "artifacts", "reports", "agents")


def test_six_operational_sections_are_active(shell):
    assert all(key in ACTIVE_SECTION_KEYS for key in OPERATIONAL_KEYS)
    assert all(next(item for item in SECTIONS if item.key == key).enabled for key in OPERATIONAL_KEYS)
    assert all(key in shell._pages for key in OPERATIONAL_KEYS)


def test_operational_page_renders_rows(qtbot):
    page = OperationalPage(
        "sessions",
        "Сессии",
        "Активные и завершённые сессии.",
        columns=(("project", "Проект"), ("state", "Состояние")),
    )
    qtbot.addWidget(page)
    page.render_rows([{"project": "AIOS", "state": "Выполняется"}])

    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "AIOS"
    assert page.table.item(0, 1).text() == "Выполняется"


def test_navigation_reaches_each_operational_page(shell):
    for key in OPERATIONAL_KEYS:
        shell.navigate_to(key)
        assert shell.current_section_key == key

