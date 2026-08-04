"""Async worker framework for the desktop shell (Desktop D2B).

Long-running or I/O-bound calls into `command_center.application` adapters run on
Qt's ``QThreadPool`` via a ``QRunnable`` — never on the GUI thread
(`docs/desktop/ARCHITECTURE.md` §9–§10). The result (or error, §12) is carried
back to the GUI thread through a small ``QObject`` signal-holder, delivered on the
receiver's thread by Qt's queued connection.

Cancellation is **cooperative** (§11): the GUI thread sets a shared, thread-safe
flag (e.g. when a newer refresh supersedes an in-flight one, or on shutdown); the
worker checks it at safe checkpoints and drops a stale result. The desktop layer
never forcibly terminates a worker thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_RUNNABLE_KEEPALIVE: set["AdapterCallRunnable"] = set()
_RUNNABLE_KEEPALIVE_LOCK = threading.Lock()


class WorkerSignals(QObject):
    """Signals emitted by :class:`AdapterCallRunnable`.

    ``QRunnable`` is not a ``QObject``, so signals live on this small holder whose
    thread affinity is the GUI thread that constructed the runnable — that is what
    makes Qt deliver the connected slots back on the GUI thread (§10 step 4).
    """

    result = Signal(object)
    error = Signal(object)
    finished = Signal()


class AdapterCallRunnable(QRunnable):
    """Run a plain callable (a ``command_center.application`` adapter method) on a
    worker thread and emit its result or error.

    Generic on purpose: it wraps *any* adapter call, not just Workspace Home
    (`ARCHITECTURE.md` §10). Pass a shared ``cancel_event`` to let the GUI thread
    supersede an in-flight call cooperatively.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        cancel_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancel = threading.Event()
        self._external_cancel = cancel_event
        self.signals = WorkerSignals()
        # Keep Python ownership: the caller holds this runnable until `finished`,
        # so Qt must not delete the C++ object (and its signal holder) out from
        # under a queued signal still in flight.
        self.setAutoDelete(False)
        # A QThreadPool does not provide Python ownership when auto-delete is
        # disabled. Keep the wrapper alive until the queued ``finished`` signal
        # has reached the GUI thread, including during a bounded shutdown.
        with _RUNNABLE_KEEPALIVE_LOCK:
            _RUNNABLE_KEEPALIVE.add(self)
        self.signals.finished.connect(self._release_keepalive)

    def cancel(self) -> None:
        """Request cooperative cancellation (idempotent, thread-safe)."""
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set() or (
            self._external_cancel is not None and self._external_cancel.is_set()
        )

    def _release_keepalive(self) -> None:
        with _RUNNABLE_KEEPALIVE_LOCK:
            _RUNNABLE_KEEPALIVE.discard(self)

    def run(self) -> None:  # executes on a worker thread
        try:
            if self.is_cancelled:  # checkpoint: superseded before we began
                return
            try:
                result = self._fn(*self._args, **self._kwargs)
            except Exception as exc:  # §12: never swallow an adapter error
                if not self.is_cancelled:
                    self.signals.error.emit(exc)
                return
            if self.is_cancelled:  # checkpoint: superseded while working
                return
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


def submit_adapter_call(
    fn: Callable[..., Any],
    *args: Any,
    pool: QThreadPool | None = None,
    cancel_event: threading.Event | None = None,
    **kwargs: Any,
) -> AdapterCallRunnable:
    """Submit ``fn(*args, **kwargs)`` to ``pool`` (default: the global pool).

    Returns the runnable so the caller can connect ``runnable.signals`` and hold a
    reference until ``finished``. Connect signals *before* the call can complete
    when determinism matters (the pool may start the worker immediately).
    """
    runnable = AdapterCallRunnable(fn, *args, cancel_event=cancel_event, **kwargs)
    (pool if pool is not None else QThreadPool.globalInstance()).start(runnable)
    return runnable
