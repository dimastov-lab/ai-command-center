"""Tests for `command_center/desktop/workers.py` (Desktop D2B).

The worker framework runs a `command_center.application` adapter call on Qt's
`QThreadPool` and delivers the result/error back to the GUI thread via signals
(`docs/desktop/ARCHITECTURE.md` §10–§12). Cancellation is cooperative — a
superseded worker drops its stale result and is never forcibly killed (§11).

`pytest-qt` against an offscreen `QApplication`.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThreadPool, QTimer

from command_center.desktop.workers import AdapterCallRunnable, submit_adapter_call


def test_worker_delivers_result_via_signal_without_blocking_gui(qtbot):
    started = threading.Event()
    release = threading.Event()

    def slow_adapter():
        started.set()
        release.wait(2.0)  # hold the worker thread so the GUI thread can prove it's free
        return {"snapshot": "ok"}

    runnable = AdapterCallRunnable(slow_adapter)
    QThreadPool.globalInstance().start(runnable)
    assert started.wait(2.0), "adapter must run on a worker thread, not the GUI thread"

    # While the worker is blocked, the GUI thread still services its event loop:
    gui_ran: list[bool] = []
    QTimer.singleShot(0, lambda: gui_ran.append(True))
    qtbot.waitUntil(lambda: gui_ran == [True], timeout=1000)

    with qtbot.waitSignal(runnable.signals.result, timeout=2000) as blocker:
        release.set()
    assert blocker.args == [{"snapshot": "ok"}]


def test_worker_propagates_adapter_error_via_signal(qtbot):
    boom = RuntimeError("adapter failed")

    def failing_adapter():
        raise boom

    runnable = AdapterCallRunnable(failing_adapter)
    with qtbot.assertNotEmitted(runnable.signals.result):
        with qtbot.waitSignal(runnable.signals.error, timeout=2000) as blocker:
            QThreadPool.globalInstance().start(runnable)
    assert blocker.args == [boom]


def test_worker_passes_args_and_kwargs_to_adapter(qtbot):
    def adapter(a, b, *, c):
        return (a, b, c)

    runnable = AdapterCallRunnable(adapter, 1, 2, c=3)
    with qtbot.waitSignal(runnable.signals.result, timeout=2000) as blocker:
        QThreadPool.globalInstance().start(runnable)
    assert blocker.args == [(1, 2, 3)]


def test_cancelled_worker_drops_its_stale_result(qtbot):
    started = threading.Event()
    release = threading.Event()

    def slow_adapter():
        started.set()
        release.wait(2.0)
        return "stale"

    runnable = AdapterCallRunnable(slow_adapter)
    QThreadPool.globalInstance().start(runnable)
    assert started.wait(2.0)

    runnable.cancel()  # a newer refresh supersedes this in-flight one
    with qtbot.assertNotEmitted(runnable.signals.result):
        with qtbot.waitSignal(runnable.signals.finished, timeout=2000):
            release.set()  # worker completes its work but must observe the cancel flag


def test_worker_cancelled_before_start_never_invokes_adapter(qtbot):
    ran: list[bool] = []

    def adapter():
        ran.append(True)
        return "x"

    cancel_event = threading.Event()
    cancel_event.set()
    runnable = AdapterCallRunnable(adapter, cancel_event=cancel_event)

    with qtbot.assertNotEmitted(runnable.signals.result):
        with qtbot.waitSignal(runnable.signals.finished, timeout=2000):
            QThreadPool.globalInstance().start(runnable)
    assert ran == [], "a worker cancelled before start must not call the adapter body"


def test_submit_adapter_call_runs_on_global_pool_and_returns_runnable(qtbot):
    release = threading.Event()

    def adapter():
        release.wait(2.0)
        return "done"

    runnable = submit_adapter_call(adapter)
    assert isinstance(runnable, AdapterCallRunnable)
    with qtbot.waitSignal(runnable.signals.result, timeout=2000) as blocker:
        release.set()
    assert blocker.args == ["done"]
