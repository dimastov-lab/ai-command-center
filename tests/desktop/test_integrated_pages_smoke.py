"""Deterministic smoke coverage for the three active Russian desktop pages."""

from command_center.desktop import i18n


def test_projects_and_settings_navigation_smoke(shell, qtbot):
    shell.resize(900, 600)
    shell.show()
    qtbot.wait(20)

    for key, expected_title in (
        ("projects", i18n.PROJECTS_TITLE),
        ("settings", i18n.SETTINGS_TITLE),
        ("home", i18n.HOME_TITLE),
    ):
        shell.sidebar.items()[key].click()
        assert shell.current_section_key == key
        page = shell.stack.currentWidget()
        assert page.header.title_label.text() == expected_title
        assert page.accessibleName()
