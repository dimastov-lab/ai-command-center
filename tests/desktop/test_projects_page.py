"""D3A Projects page edit/validate/save flows."""

from __future__ import annotations

from command_center.desktop.pages.projects import ProjectsPage


class StubProjectsAdapter:
    def __init__(self) -> None:
        self.projects = {
            "AIOS": {
                "id": "AIOS",
                "display_name": "AIOS",
                "repository_path": None,
                "sensitive": False,
            },
            "BANK": {
                "id": "BANK",
                "display_name": "Банковская стратегия",
                "repository_path": "/repo/bank",
                "sensitive": True,
            },
        }
        self.validation_result = (True, "OK")
        self.validated: list[str] = []
        self.saved: list[tuple[str, str | None]] = []

    def list_projects(self):
        return self.projects

    def validate_repository_path(self, path: str):
        self.validated.append(path)
        return self.validation_result

    def save_repository_path(self, project_id: str, path: str | None):
        self.saved.append((project_id, path))
        self.projects[project_id]["repository_path"] = path


def _page(qtbot):
    adapter = StubProjectsAdapter()
    page = ProjectsPage(adapter)
    qtbot.addWidget(page)
    page.show()
    return page, adapter


def test_projects_page_renders_all_projects_and_existing_path(qtbot):
    page, _adapter = _page(qtbot)

    assert set(page.editors()) == {"AIOS", "BANK"}
    assert page.editors()["AIOS"].path_edit.text() == ""
    assert page.editors()["BANK"].path_edit.text() == "/repo/bank"


def test_valid_path_is_validated_then_saved(qtbot):
    page, adapter = _page(qtbot)
    editor = page.editors()["AIOS"]
    editor.path_edit.setText("  /repo/aios  ")

    editor.save_button.click()

    assert adapter.validated == ["/repo/aios"]
    assert adapter.saved == [("AIOS", "/repo/aios")]
    assert editor.status_label.text() == "Путь сохранён."
    assert editor.path_edit.text() == "/repo/aios"


def test_invalid_path_shows_core_error_and_is_not_saved(qtbot):
    page, adapter = _page(qtbot)
    adapter.validation_result = (False, "Путь не существует: /missing")
    editor = page.editors()["AIOS"]
    editor.path_edit.setText("/missing")

    editor.save_button.click()

    assert adapter.validated == ["/missing"]
    assert adapter.saved == []
    assert editor.status_label.text() == "Путь не существует: /missing"


def test_clear_path_does_not_route_through_nonempty_validation(qtbot):
    page, adapter = _page(qtbot)
    editor = page.editors()["BANK"]

    editor.clear_button.click()

    assert adapter.validated == []
    assert adapter.saved == [("BANK", None)]
    assert editor.path_edit.text() == ""
    assert editor.status_label.text() == "Путь очищен."
    assert not editor.clear_button.isEnabled()


def test_save_failure_is_presented_as_russian_error(qtbot):
    page, adapter = _page(qtbot)

    def fail(_project_id: str, _path: str | None) -> None:
        raise OSError("disk full")

    adapter.save_repository_path = fail
    editor = page.editors()["AIOS"]
    editor.path_edit.setText("/repo/aios")

    editor.save_button.click()

    assert editor.status_label.text() == "Не удалось сохранить путь. Повторите попытку."
    assert "disk full" not in editor.status_label.text()


def test_project_editor_accessibility_and_keyboard_focus(qtbot):
    page, _adapter = _page(qtbot)
    editor = page.editors()["AIOS"]

    assert editor.accessibleName() == "Настройка проекта «AIOS»"
    assert editor.path_edit.accessibleName() == "Путь к репозиторию проекта «AIOS»"
    assert editor.save_button.accessibleName() == "Сохранить путь проекта «AIOS»"
    assert editor.clear_button.accessibleName() == "Очистить путь проекта «AIOS»"
