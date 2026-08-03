"""Tests for the native Workspace Home page rendering (Desktop D2C).

`HomePage` starts in an EmptyState (no data wired yet) and, once given a snapshot
via :meth:`render_snapshot`, builds the populated Workspace Home from the D2C
components: a header MetricCard strip, one ProjectCard per project (with worktrees
for ``ok`` projects), and Active/Recent runs, Artifacts, Reports, Activity
sections. Loading is async through a `WorkspaceHomeAdapter`; the GUI thread is
never blocked.
"""

from __future__ import annotations

import gc
import threading
import weakref

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy

from command_center.desktop import i18n, tokens
from command_center.desktop.components.empty_state import EmptyState
from command_center.desktop.components.status_badge import StatusBadge, StatusVariant
from command_center.desktop.components.worktree_row import WorktreeRow
from command_center.desktop.pages.home import HomePage


def _snapshot(**over) -> dict:
    base = {
        "projects": [
            {
                "id": "AIOS", "display_name": "AIOS", "sensitive": False,
                "repository_path": "/r", "repository_state": "ok",
                "task_count": 3, "active_run_count": 1,
            },
            {
                "id": "BANK", "display_name": "BANK", "sensitive": True,
                "repository_path": None, "repository_state": "unconfigured",
                "task_count": 0, "active_run_count": 0,
            },
        ],
        "worktrees_by_project": {
            "AIOS": {"state": "ok", "worktrees": [{"path": "/r", "branch": "main", "head": "abc1234567"}]},
            "BANK": {"state": "unconfigured", "worktrees": []},
        },
        "active_runs": [
            {"source": "v2", "run_id": "r1", "project": "AIOS", "task_type": "implementation", "state": "RUNNING"}
        ],
        "recent_runs": [
            {"source": "v2", "run_id": "r0", "project": "AIOS", "task_type": "review", "state": "COMPLETED"}
        ],
        "recent_activity": [
            {"event_type": "run_completed", "project": "AIOS", "ts": "2026-07-29T10:00:00"}
        ],
        "artifacts": [
            {"project": "AIOS", "task_type": "implementation", "created_at": 1730000000,
             "nav_target": "AIOS/artifacts", "path": "/gen/x.md"}
        ],
        "reports": [
            {"run_id": "r0", "source": "v2", "project": "AIOS", "verdict": "APPROVED_FOR_COMMIT",
             "severity_counts": {}, "created_at": 1730000000}
        ],
    }
    base.update(over)
    return base


def test_home_starts_in_empty_state(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    assert page.findChild(EmptyState) is not None


def test_render_snapshot_makes_one_project_card_per_project_in_order(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    assert [c.project_id for c in page.project_cards()] == ["AIOS", "BANK"]
    assert page.findChild(EmptyState) is None  # populated content replaced the empty state


def test_render_snapshot_metric_strip_reflects_counts(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    metrics = {m.label_text(): m.value_text() for m in page.metric_cards()}
    assert metrics[i18n.METRIC_PROJECTS] == "2"
    assert metrics[i18n.METRIC_ACTIVE_RUNS] == "1"
    assert metrics[i18n.METRIC_REPORTS] == "1"


def test_metric_strip_shares_available_width_between_all_captions(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.resize(900, 700)
    page.render_snapshot(_snapshot())
    page.show()
    qtbot.wait(20)

    cards = page.metric_cards()
    assert len(cards) == 5
    assert all(
        card.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding
        for card in cards
    )
    assert min(card.width() for card in cards) >= 130


def test_home_shows_separate_russian_aios_core_card_with_source_and_evidence(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(
        _snapshot(
            aios_core={
                "readiness": "contract_pending",
                "source": "configuration",
                "version": None,
                "health": "healthy",
                "capabilities": ["memory-api"],
                "gates": ["contract-tests"],
                "evidence": ["Публичный контракт AIOS Core ожидается"],
                "detail": None,
            }
        )
    )

    card = page.aios_core_card()
    assert card is not None
    visible_text = " ".join(label.text() for label in card.findChildren(QLabel))
    assert "AIOS Core" in visible_text
    assert "Контракт ожидается" in visible_text
    assert "Источник: конфигурация" in visible_text
    assert "Здоровье: исправен" in visible_text
    assert "Возможности: memory-api" in visible_text
    assert "Гейты приёмки: contract-tests" in visible_text
    assert "Публичный контракт AIOS Core ожидается" in visible_text.replace("\u200b", "")
    badge = card.findChild(StatusBadge)
    assert badge is not None
    assert badge.variant is StatusVariant.INFO
    assert card.accessibleName().startswith("AIOS Core:")


def test_aios_core_card_bounds_long_contract_values_at_desktop_width(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    long_id = "x" * 256
    page.render_snapshot(
        _snapshot(
            aios_core={
                "readiness": "not_ready",
                "source": "AIOS API",
                "version": "v" * 256,
                "health": "h" * 256,
                "capabilities": [long_id] * 10,
                "gates": [long_id] * 10,
                "evidence": [f"build:{long_id}"] * 100,
                "detail": None,
            }
        )
    )
    page.resize(900, 700)
    page.show()
    qtbot.wait(20)

    card = page.aios_core_card()
    assert card is not None
    assert card.minimumSizeHint().width() <= 818
    assert len(card.accessibleDescription()) <= 1_024
    assert len(card.findChildren(QLabel)) <= 10
    visible_text = " ".join(label.text() for label in card.findChildren(QLabel))
    assert "ещё 5" in visible_text


def test_render_snapshot_populates_all_sections(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    assert len(page.run_summaries()) == 2  # 1 active + 1 recent
    assert len(page.report_rows()) == 1
    assert len(page.artifact_rows()) == 1
    assert len(page.activity_items()) == 1


def test_render_snapshot_shows_worktrees_only_for_ok_projects(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    worktrees = page.findChildren(WorktreeRow)
    assert len(worktrees) == 1  # AIOS is ok; BANK is unconfigured


def test_render_snapshot_is_idempotent(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    page.render_snapshot(_snapshot())  # re-render must not accumulate
    assert len(page.project_cards()) == 2


def test_home_never_requires_horizontal_scrolling_at_supported_widths(qtbot):
    """Long repository data must not make the main page wider than its viewport."""
    page = HomePage()
    qtbot.addWidget(page)
    snapshot = _snapshot(
        worktrees_by_project={
            "AIOS": {
                "state": "ok",
                "worktrees": [{
                    "path": "/very/" + ("long-directory/" * 30),
                    "branch": "feature/" + ("long-branch-" * 20),
                    "head": "abc1234567",
                }],
            },
            "BANK": {"state": "unconfigured", "worktrees": []},
        },
        artifacts=[{
            "project": "AIOS",
            "task_type": "implementation",
            "created_at": 1730000000,
            "nav_target": "AIOS/artifacts",
            "path": "/generated/" + ("long-artifact-name-" * 30) + ".md",
        }],
    )
    page.render_snapshot(snapshot)

    for width in (620, 900):
        page.resize(width, 700)
        page.show()
        qtbot.wait(20)
        assert page._scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        assert page._scroll.horizontalScrollBar().maximum() == 0
        assert page._content.width() <= page._scroll.viewport().width()


def test_configure_action_emits_navigate_to_projects(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    bank = next(c for c in page.project_cards() if c.project_id == "BANK")
    assert bank.configure_button is not None
    with qtbot.waitSignal(page.navigate_requested, timeout=1000) as blocker:
        bank.configure_button.click()
    assert blocker.args == ["projects"]


def test_apply_palette_colours_rendered_badges(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.apply_palette(tokens.DARK)
    page.render_snapshot(_snapshot())
    aios = next(c for c in page.project_cards() if c.project_id == "AIOS")
    assert tokens.DARK.status_success in aios.health_badge.styleSheet()


def test_load_fetches_via_adapter_without_blocking_gui(qtbot):
    class _StubAdapter:
        def snapshot(self, **_kwargs):
            return _snapshot()

    page = HomePage()
    qtbot.addWidget(page)
    runnable = page.load(_StubAdapter())
    assert runnable is not None
    qtbot.waitUntil(lambda: len(page.project_cards()) == 2, timeout=2000)


def test_slow_aios_status_does_not_delay_or_replace_local_home(qtbot):
    status_started = threading.Event()
    release_status = threading.Event()

    class _Adapter:
        def snapshot(self):
            return _snapshot()

        def aios_core_status(self):
            status_started.set()
            release_status.wait(2.0)
            return {
                "readiness": "offline",
                "source": "AIOS API",
                "version": None,
                "health": None,
                "capabilities": [],
                "gates": [],
                "evidence": [],
                "detail": "Публичный API AIOS Core недоступен",
            }

    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    page = HomePage()
    qtbot.addWidget(page)
    page.load(_Adapter(), pool=pool)

    assert status_started.wait(1.0)
    qtbot.waitUntil(lambda: len(page.project_cards()) == 2, timeout=1000)
    assert page.aios_core_card() is None

    release_status.set()
    qtbot.waitUntil(lambda: page.aios_core_card() is not None, timeout=1000)
    assert len(page.project_cards()) == 2
    assert pool.waitForDone(2000)


def test_home_displays_provider_readiness_without_claiming_unknown_auth(qtbot):
    class _Adapter:
        def snapshot(self):
            return _snapshot()

        def provider_capabilities(self):
            return [
                {
                    "provider_id": "antigravity",
                    "display_name": "Antigravity",
                    "readiness": "login_required",
                    "provenance": "локальный исполняемый файл",
                    "detail": None,
                },
                {
                    "provider_id": "ollama",
                    "display_name": "Ollama",
                    "readiness": "available",
                    "provenance": "локальная служба Ollama",
                    "detail": "Доступно моделей: 6",
                },
            ]

    page = HomePage()
    qtbot.addWidget(page)
    page.load(_Adapter())
    qtbot.waitUntil(lambda: page.provider_capabilities_card() is not None, timeout=2000)
    card = page.provider_capabilities_card()
    assert card is not None
    text = " ".join(label.text() for label in card.findChildren(QLabel))
    assert "Antigravity — установлен, требуется вход" in text
    assert "Ollama — доступен" in text
    assert "Доступно моделей: 6" in text


def test_load_without_adapter_is_a_noop(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    assert page.load() is None
    assert page.findChild(EmptyState) is not None


def test_page_keeps_runnable_alive_until_finished_even_after_gc(qtbot):
    started = threading.Event()
    release = threading.Event()

    class _SlowAdapter:
        def snapshot(self):
            started.set()
            release.wait(2.0)
            return _snapshot()

    pool = QThreadPool()
    page = HomePage()
    qtbot.addWidget(page)
    runnable = page.load(_SlowAdapter(), pool=pool)
    assert runnable is not None
    assert started.wait(2.0)
    runnable_ref = weakref.ref(runnable)
    del runnable
    gc.collect()

    assert runnable_ref() is not None
    assert page.active_worker_count() == 1
    release.set()
    qtbot.waitUntil(lambda: page.active_worker_count() == 0, timeout=2000)
    assert pool.waitForDone(2000)


def test_newer_refresh_wins_when_older_result_finishes_later(qtbot):
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0

    class _OutOfOrderAdapter:
        def snapshot(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                release_first.wait(2.0)
                return _snapshot(projects=[{**_snapshot()["projects"][0], "id": "OLD"}])
            return _snapshot(projects=[{**_snapshot()["projects"][0], "id": "NEW"}])

    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    adapter = _OutOfOrderAdapter()
    page = HomePage()
    qtbot.addWidget(page)
    page.load(adapter, pool=pool)
    assert first_started.wait(2.0)
    page.load(adapter, pool=pool)
    qtbot.waitUntil(
        lambda: [card.project_id for card in page.project_cards()] == ["NEW"],
        timeout=2000,
    )
    release_first.set()
    assert pool.waitForDone(2000)
    qtbot.wait(50)
    assert [card.project_id for card in page.project_cards()] == ["NEW"]


def test_shutdown_drops_queued_worker_delivery(qtbot):
    started = threading.Event()
    release = threading.Event()

    class _SlowAdapter:
        def snapshot(self):
            started.set()
            release.wait(2.0)
            return _snapshot()

    pool = QThreadPool()
    page = HomePage()
    qtbot.addWidget(page)
    page.load(_SlowAdapter(), pool=pool)
    assert started.wait(2.0)
    page.shutdown_workers()
    release.set()
    assert pool.waitForDone(2000)
    qtbot.wait(50)
    assert page.project_cards() == []
    assert page.load(_SlowAdapter(), pool=pool) is None


def test_adapter_error_is_redacted_from_ui(qtbot):
    secret = "token=super-secret"

    class _FailingAdapter:
        def snapshot(self):
            raise RuntimeError(secret)

    page = HomePage()
    qtbot.addWidget(page)
    page.load(_FailingAdapter())
    qtbot.waitUntil(
        lambda: any(
            label.text() == i18n.HOME_LOAD_ERROR_DETAIL
            for label in page.findChildren(QLabel)
        ),
        timeout=2000,
    )
    assert all(secret not in label.text() for label in page.findChildren(QLabel))
