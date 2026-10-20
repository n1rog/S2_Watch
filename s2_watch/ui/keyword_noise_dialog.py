"""Reports → Keyword Noise: which keywords flag reports you then ignore, and stopping them."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from s2_watch.ui.events_view import SortKeyItem

MIN_IGNORED = 2      # fewer ignored reports than this say nothing about a word


class KeywordNoiseDialog(QDialog):
    stop_requested = Signal(str, str)      # category, keyword
    resume_requested = Signal(str, str)

    COLUMNS = ("Category", "Keyword", "Reports", "Ignored", "Share ignored", "Ignored from")

    def __init__(self, store, excluded: dict[str, list[str]], parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Keyword Noise")
        self.resize(900, 620)
        intro = QLabel(
            "Keywords that flagged newsworthy reports you then marked Ignored, noisiest first. "
            "<b>Stop using</b> a word and it no longer makes a report news in that category (other words "
            "still can), for every source: radio, official feeds and news. Ignored reports from official "
            "feeds usually mean the item was minor (a small fire), not that the word is wrong.")
        intro.setWordWrap(True)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.empty = QLabel(f"No keyword has led to {MIN_IGNORED} or more ignored reports yet.")
        self.stop_button = QPushButton("Stop using this word")
        self.stop_button.clicked.connect(self._stop)
        stopped_label = QLabel("<b>Words you stopped using</b>")
        self.stopped = QTableWidget(0, 2)
        self.stopped.setHorizontalHeaderLabels(("Category", "Keyword"))
        self.stopped.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stopped.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stopped.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stopped.verticalHeader().setVisible(False)
        self.stopped.horizontalHeader().setStretchLastSection(True)
        self.stopped.setMaximumHeight(150)
        self.stopped.itemSelectionChanged.connect(self._selection_changed)
        self.resume_button = QPushButton("Use again")
        self.resume_button.clicked.connect(self._resume)
        row = QHBoxLayout()
        row.addWidget(self.stop_button)
        row.addStretch(1)
        row.addWidget(self.resume_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addWidget(stopped_label)
        layout.addWidget(self.stopped)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.refresh(excluded)

    def refresh(self, excluded: dict[str, list[str]]) -> None:
        stopped = {(category, word) for category, words in excluded.items() for word in words}
        rows = [row for row in self.store.keyword_noise()
                if row["ignored"] >= MIN_IGNORED and (row["category"], row["keyword"]) not in stopped]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            share = row["ignored"] / row["reports"]
            origin = ", ".join(part for part in (f"radio {row['ignored_radio']}" if row["ignored_radio"] else "",
                                                 f"feeds {row['ignored_feeds']}" if row["ignored_feeds"] else "") if part)
            cells = (QTableWidgetItem(row["category"]), QTableWidgetItem(row["keyword"]),
                     SortKeyItem(str(row["reports"]), row["reports"]), SortKeyItem(str(row["ignored"]), row["ignored"]),
                     SortKeyItem(f"{share:.0%}", share), QTableWidgetItem(origin))
            for column, item in enumerate(cells):
                self.table.setItem(index, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(4, Qt.SortOrder.DescendingOrder)      # noisiest first
        self.empty.setVisible(not rows)
        self.stopped.setRowCount(0)
        for category, word in sorted(stopped):
            index = self.stopped.rowCount()
            self.stopped.insertRow(index)
            self.stopped.setItem(index, 0, QTableWidgetItem(category))
            self.stopped.setItem(index, 1, QTableWidgetItem(word))
        self._selection_changed()

    @staticmethod
    def _chosen(table: QTableWidget) -> tuple[str, str] | None:
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        return table.item(row, 0).text(), table.item(row, 1).text()

    def _selection_changed(self) -> None:
        self.stop_button.setEnabled(self._chosen(self.table) is not None)
        self.resume_button.setEnabled(self._chosen(self.stopped) is not None)

    def _stop(self) -> None:
        chosen = self._chosen(self.table)
        if chosen:
            self.stop_requested.emit(*chosen)

    def _resume(self) -> None:
        chosen = self._chosen(self.stopped)
        if chosen:
            self.resume_requested.emit(*chosen)
