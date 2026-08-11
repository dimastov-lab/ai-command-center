"""D2-D4 data-wired journeys through the real application ports (issue #196).

Unlike the adapter unit tests (which use fakes), these tests launch the full
offscreen ``AppShell`` wired to a *real* ``ExecutionCenterAPI`` over a temp
runtime database — the exact production wiring of ``app.run`` — and prove:

- first run (empty data root): every native section loads without crashing and
  reports its empty/populated state, never an error;
- seeded data root: operational sections populate with rows produced through
  the one execution engine (``api.start_run`` → supervisor), read back only via
  the narrow ``OperationsAdapter`` port;
- restart: a fresh shell over the same settings file and the same data root
  reuses persisted preferences and still populates from the same port.
"""

from __future__ import annotations

import os

import pytest

from PySide6.QtCore import QSettings

from command_center import project_config
from command_center.application.operations_adapter import OperationsAdapter
from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter
from command_center.desktop import i18n
from command_center.desktop.app import build_shell
from command_center.desktop.settings import SettingsStore
from command_center.desktop.theme import ThemeMode
from command_center.runtime.api import ExecutionCenterAPI

OPERATIONAL_KEYS = ("sessions", "execution", "git", "artifacts", "reports", "agents")
WAIT_MS = 10_000


def _wire_real_ports(shell, tmp_path) -> ExecutionCenterAPI:
    """Wire the shell exactly as ``app.run`` does, over an isolated data root."""
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    home_adapter = WorkspaceHomeAdapter(execution_center_api=api)
    shell.load_workspace_home(
        home_adapter,
        OperationsAdapter(workspace_home_adapter=home_adapter),
    )
    return api


def _wait_loaded(qtbot, page) -> None:
    """Wait until the section's async worker settled (rows or explicit empty)."""
    qtbot.waitUntil(
        lambda: page.status.text()
        not in (i18n.OPERATIONS_NOT_LOADED, i18n.OPERATIONS_LOADING),
        timeout=WAIT_MS,
    )


def test_first_run_empty_data_root_loads_every_section_without_error(
    shell, qtbot, tmp_path
):
    """First-run journey: fresh empty runtime DB, no repository configured."""
    _wire_real_ports(shell, tmp_path)
    shell.show()

    for key in OPERATIONAL_KEYS:
        shell.navigate_to(key)
        assert shell.current_section_key == key
        page = shell._operational_pages[key]
        _wait_loaded(qtbot, page)
        assert page.status.text() != i18n.OPERATIONS_ERROR

    # Empty data root: no sessions and no artifacts exist yet.
    assert shell._operational_pages["sessions"].rows == []
    assert shell._operational_pages["artifacts"].rows == []
    # The git section still lists the canonical projects (all unconfigured).
    assert shell._operational_pages["git"].table.rowCount() > 0

    assert shell.shutdown() is True


@pytest.mark.skipif(
    os.name != "posix",
    reason=(
        "The seeded journey executes a real run through Supervisor, which "
        "structurally refuses non-POSIX hosts (no waitid/WNOWAIT process-tree "
        "ownership) -- the same refusal production enforces."
    ),
)
def test_sections_populate_from_seeded_data_root_through_the_port(
    shell, qtbot, tmp_path, git_repo, configure_project_repo, fake_claude
):
    """A run executed by the one real engine shows up in the native tables."""
    configure_project_repo("AIOS", git_repo)
    project_config.save_repository_path("AIOS", str(git_repo))
    api = _wire_real_ports(shell, tmp_path)

    run = api.start_run(
        project="AIOS",
        repository_path=str(git_repo),
        task_type="implementation",
        instruction="do work",
        confirmed=True,
    )
    api.supervisor.wait_for_run(run["id"], timeout=10)
    shell.show()

    sessions = shell._operational_pages["sessions"]
    shell.navigate_to("sessions")
    qtbot.waitUntil(lambda: sessions.table.rowCount() >= 1, timeout=WAIT_MS)
    assert any(row.get("project") == "AIOS" for row in sessions.rows)
    assert sessions.status.text() == i18n.OPERATIONS_ROWS.format(
        count=sessions.table.rowCount()
    )

    execution = shell._operational_pages["execution"]
    shell.navigate_to("execution")
    qtbot.waitUntil(lambda: execution.table.rowCount() >= 1, timeout=WAIT_MS)
    assert any(row.get("run_id") == run["id"] for row in execution.rows)

    git_page = shell._operational_pages["git"]
    shell.navigate_to("git")
    _wait_loaded(qtbot, git_page)
    aios_row = next(row for row in git_page.rows if row.get("project") == "AIOS")
    assert aios_row["path"] == str(git_repo)

    assert shell.shutdown() is True


def test_restart_reuses_preferences_and_stays_data_wired(
    qtbot, qapp, settings_store, settings_file, tmp_path
):
    """Restart journey: preferences persist and the fresh shell reads the same
    data root through a freshly constructed port, without crashing."""
    first, _theme = build_shell(qapp, settings_store)
    qtbot.addWidget(first)
    _wire_real_ports(first, tmp_path)
    first.show()

    first.navigate_to("settings")
    first._settings_page.buttons()[ThemeMode.DARK].click()
    form = first._settings_page.form
    form.selected_project_edit.setText("AIOS")
    form.save_workspace_button.click()
    assert first.shutdown() is True

    reopened = SettingsStore(QSettings(str(settings_file), QSettings.IniFormat))
    assert reopened.theme_mode() is ThemeMode.DARK
    assert reopened.selected_project() == "AIOS"

    second, theme = build_shell(qapp, reopened)
    qtbot.addWidget(second)
    _wire_real_ports(second, tmp_path)
    second.show()

    assert theme.mode is ThemeMode.DARK
    assert (
        second._settings_page.form.selected_project_edit.text() == "AIOS"
    )

    sessions = second._operational_pages["sessions"]
    second.navigate_to("sessions")
    _wait_loaded(qtbot, sessions)
    assert sessions.status.text() != i18n.OPERATIONS_ERROR

    assert second.shutdown() is True
