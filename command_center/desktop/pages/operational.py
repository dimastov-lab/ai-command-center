"""Reusable non-blocking table page for native operational sections."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .. import i18n
from ..workers import AdapterCallRunnable
from .base_page import BasePage


class OperationalPage(BasePage):
    def __init__(
        self,
        section_key: str,
        title: str,
        description: str,
        *,
        columns: tuple[tuple[str, str], ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(section_key, title, description, parent)
        self.columns = columns
        self._rows: list[dict] = []
        self._request_id = 0
        self._runnables: dict[int, AdapterCallRunnable] = {}
        self._accept_signals = True

        self.status = QLabel(i18n.OPERATIONS_NOT_LOADED)
        self.status.setObjectName("RowMeta")
        self.add_content(self.status)

        self.table = QTableWidget(0, len(columns))
        self.table.setObjectName(f"{section_key.title()}Table")
        self.table.setHorizontalHeaderLabels([label for _, label in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAccessibleName(i18n.OPERATIONS_TABLE_ACCESSIBLE.format(title=title))
        self.add_content(self.table, stretch=1)

    @property
    def rows(self) -> list[dict]:
        return list(self._rows)

    def render_rows(self, rows: list[dict]) -> None:
        self._rows = [dict(row) for row in rows]
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (key, _label) in enumerate(self.columns):
                self.table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(i18n.operation_value(key, row.get(key))),
                )
        self.table.resizeColumnsToContents()
        self.status.setText(
            i18n.OPERATIONS_ROWS.format(count=len(rows)) if rows else i18n.OPERATIONS_EMPTY
        )

    def load(
        self,
        fetch: Callable[[], list[dict]],
        *,
        cancel_event: threading.Event | None = None,
        pool: QThreadPool | None = None,
    ) -> AdapterCallRunnable | None:
        if not self._accept_signals:
            return None
        self._request_id += 1
        request_id = self._request_id
        for old_id, old in tuple(self._runnables.items()):
            if old_id != request_id:
                old.cancel()
        self.status.setText(i18n.OPERATIONS_LOADING)
        runnable = AdapterCallRunnable(fetch, cancel_event=cancel_event)
        self._runnables[request_id] = runnable
        page_ref = weakref.ref(self)

        def deliver(rows: list[dict]) -> None:
            page = page_ref()
            if page is not None and page._accept_signals and request_id == page._request_id:
                page.render_rows(rows)

        def fail(_exc: Exception) -> None:
            page = page_ref()
            if page is not None and page._accept_signals and request_id == page._request_id:
                page.status.setText(i18n.OPERATIONS_ERROR)

        def release() -> None:
            page = page_ref()
            if page is not None:
                page._runnables.pop(request_id, None)

        runnable.signals.result.connect(deliver)
        runnable.signals.error.connect(fail)
        runnable.signals.finished.connect(release)
        (pool or QThreadPool.globalInstance()).start(runnable)
        return runnable

    def shutdown_workers(self) -> None:
        self._accept_signals = False
        for runnable in tuple(self._runnables.values()):
            runnable.cancel()
        self._runnables.clear()

