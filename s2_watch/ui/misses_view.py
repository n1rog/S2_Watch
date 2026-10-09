"""The Possible Misses tab: radio messages no watch form kept that the model thinks are news."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from s2_watch.analysis.ollama import readable_text


class MissesView(QWidget):
    add_requested = Signal(str, object, object)   # key, Message, AnalysisResult
    count_changed = Signal(int)

    COLUMNS = ("Heard", "Source", "Model's headline", "Category", "Urgency", "Confidence", "Message")

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self._misses = []
        self.explain = QLabel(
            "Radio messages that none of your watch forms kept, read in the background by your Ollama model. "
            "These are the ones it thinks are news: add them as reports, or dismiss them.")
        self.explain.setWordWrap(True)
        self.status = QLabel()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(self.COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        add = QPushButton("Add as report")
        add.setToolTip("Save the selected messages as newsworthy reports (Newsworthiness, map, events)")
        add.clicked.connect(self._add)
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(lambda: self._set_status("dismissed"))
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(add)
        buttons.addWidget(dismiss)
        layout = QVBoxLayout(self)
        layout.addWidget(self.explain)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        self._misses = self.store.possible_misses("new")
        self.table.setRowCount(0)
        for key, message, result in self._misses:
            row = self.table.rowCount()
            self.table.insertRow(row)
            body = " ".join(readable_text(message).split())
            values = (self.format_time(message.received_at), message.source_application, result.summary,
                      result.category, result.urgency, f"{result.confidence:.0%}", body)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, key)
                if column == len(values) - 1:
                    item.setToolTip(body)
                self.table.setItem(row, column, item)
        self.count_changed.emit(len(self._misses))

    def show_progress(self, checked: int, waiting: int, error: str = "") -> None:
        counts = self.store.missed_counts()
        text = (f"Checked {sum(counts.values())} messages so far ({checked} this session); {waiting} waiting. "
                f"Found {counts.get('new', 0) + counts.get('added', 0) + counts.get('dismissed', 0)} possible misses.")
        if error:
            text += f"  Ollama unavailable: {error}"
        self.status.setText(text)

    def _selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        keys = [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in rows]
        return [miss for miss in self._misses if miss[0] in keys]

    def _add(self) -> None:
        for key, message, result in self._selected():
            self.add_requested.emit(key, message, result)
        self.refresh()

    def _set_status(self, status: str) -> None:
        for key, _message, _result in self._selected():
            self.store.set_missed_status(key, status)
        self.refresh()
