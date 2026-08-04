"""Tests for `command_center/desktop/components/project_card.py` (Desktop D2C).

`ProjectCard` renders one entry of ``snapshot["projects"]`` per
`WORKSPACE_HOME_SPEC.md` §2–§3: title, a repository-health `StatusBadge` (the
§3 mapping that must not drift), a sensitive badge for BANK/LEGAL, the "Tasks" /
"Active runs" counts, and a "configure repository path" action only when the repo
is unconfigured. Navigation-only: the action emits, it does not mutate.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from command_center.desktop import i18n, tokens
from command_center.desktop.components.project_card import ProjectCard, repository_health
from command_center.desktop.components.status_badge import StatusVariant


def _project(**over) -> dict:
    base = dict(
        id="AIOS",
        display_name="AIOS",
        sensitive=False,
        repository_path=None,
        repository_state="unconfigured",
        task_count=0,
        active_run_count=0,
    )
    base.update(over)
    return base


@pytest.mark.parametrize(
    "state,variant",
    [
        ("unconfigured", StatusVariant.NEUTRAL),
        ("invalid_path", StatusVariant.WARNING),
        ("not_git_repo", StatusVariant.WARNING),
        ("ok", StatusVariant.SUCCESS),
    ],
)
def test_repository_health_maps_state_to_variant_and_label(state, variant):
    got_variant, got_label = repository_health(state)
    assert got_variant is variant
    assert got_label == i18n.REPO_STATE_LABELS[state]


def test_card_shows_display_name_and_health_badge(qtbot):
    card = ProjectCard(_project(display_name="AIOS", repository_state="ok"))
    qtbot.addWidget(card)
    assert card.project_id == "AIOS"
    assert card.title_label.text() == "AIOS"
    assert card.health_badge.variant is StatusVariant.SUCCESS
    assert card.health_badge.text() == i18n.REPO_STATE_LABELS["ok"]


def test_card_renders_sensitive_badge_only_for_sensitive_projects(qtbot):
    plain = ProjectCard(_project(sensitive=False))
    qtbot.addWidget(plain)
    assert plain.sensitive_badge is None

    sensitive = ProjectCard(_project(id="BANK", display_name="BANK", sensitive=True))
    qtbot.addWidget(sensitive)
    assert sensitive.sensitive_badge is not None
    assert sensitive.sensitive_badge.variant is StatusVariant.SENSITIVE
    assert sensitive.sensitive_badge.text() == i18n.BADGE_SENSITIVE


def test_card_shows_task_and_active_run_counts_with_exact_labels(qtbot):
    card = ProjectCard(_project(task_count=7, active_run_count=2, repository_state="ok"))
    qtbot.addWidget(card)
    texts = card.stats_text()
    assert any(i18n.PROJECT_TASKS_LABEL in t and "7" in t for t in texts)
    assert any(i18n.PROJECT_ACTIVE_RUNS_LABEL in t and "2" in t for t in texts)


def test_configure_action_appears_only_when_unconfigured_and_emits_id(qtbot):
    configured = ProjectCard(_project(repository_state="ok"))
    qtbot.addWidget(configured)
    assert configured.configure_button is None

    card = ProjectCard(_project(id="LEGAL", repository_state="unconfigured"))
    qtbot.addWidget(card)
    assert isinstance(card.configure_button, QPushButton)
    with qtbot.waitSignal(card.configure_requested, timeout=1000) as blocker:
        card.configure_button.click()
    assert blocker.args == ["LEGAL"]


def test_apply_palette_propagates_to_child_badges(qtbot):
    card = ProjectCard(_project(id="BANK", sensitive=True, repository_state="ok"))
    qtbot.addWidget(card)
    card.apply_palette(tokens.DARK)
    assert tokens.DARK.status_success in card.health_badge.styleSheet()
    assert tokens.DARK.status_sensitive in card.sensitive_badge.styleSheet()
