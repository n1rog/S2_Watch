"""The Possible Misses tab: radio messages no watch form kept that the model thinks are news.

The operator triages them: add as a report (correcting the model's verdict first, and
optionally adding a keyword so the rules catch the next one), dismiss as not news, delete,
or restore a dismissed one. Decisions become examples for the model.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from s2_watch.analysis.base import AnalysisResult
from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.analysis.ollama import readable_text
from s2_watch.events import key_terms
from s2_watch.missed_news import is_fragment

URGENCIES = ("low", "medium", "high")


def suggested_keyword(message, result: AnalysisResult) -> str:
    """A word that would have caught this message: the model's own matched word, when it
    gave one that is in the message. No guess otherwise: a common word ("expected") would
    flag a flood of ordinary traffic, so the operator types it."""
    body = readable_text(message).lower()
    for term in result.matched_terms:
        term = term.strip().lower()
        if len(term) >= 4 and term in body and term.upper() in key_terms(body):
            return term
    return ""


class AddMissDialog(QDialog):
    """Correct the model's verdict before a possible miss becomes a report."""

    COMMON = 0.02   # a keyword in more than 2% of radio traffic would flag too much

    def __init__(self, message, result: AnalysisResult, parent=None, count_matches=None) -> None:
        super().__init__(parent)
        self.count_matches = count_matches
        self.setWindowTitle("Add as report")
        self.resize(640, 420)
        body = " ".join(readable_text(message).split())
        text = QPlainTextEdit(body)
        text.setReadOnly(True)
        text.setMaximumHeight(110)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.headline = QLineEdit(result.summary if result.summary != message.raw_content else body[:100])
        self.category = QComboBox()
        self.category.addItems(NEWS_CATEGORIES)
        self.category.setCurrentText(result.category if result.category in NEWS_CATEGORIES else NEWS_CATEGORIES[0])
        self.urgency = QComboBox()
        self.urgency.addItems(URGENCIES)
        self.urgency.setCurrentText(result.urgency if result.urgency in URGENCIES else "medium")
        self.place = QLineEdit()
        self.place.setPlaceholderText("Optional, e.g. Nashville, TN — otherwise taken from the message")
        self.add_keyword = QCheckBox("Also catch messages like this with a keyword:")
        self.add_keyword.setToolTip("Adds the word to this category's watch form and to the keyword rules, so the next "
                                    "message with it is kept and flagged straight away")
        self.keyword = QLineEdit(suggested_keyword(message, result))
        self.keyword.setPlaceholderText("a distinctive word, e.g. a storm or place name")
        self.add_keyword.setChecked(bool(self.keyword.text()))
        self.keyword_hint = QLabel()
        self.keyword_hint.setWordWrap(True)
        self.keyword.textChanged.connect(self._keyword_changed)
        form = QFormLayout()
        form.addRow("Message", text)
        form.addRow("Headline", self.headline)
        form.addRow("Category", self.category)
        form.addRow("Urgency", self.urgency)
        form.addRow("Place", self.place)
        keyword_row = QHBoxLayout()
        keyword_row.addWidget(self.add_keyword)
        keyword_row.addWidget(self.keyword, 1)
        self._keyword_changed(self.keyword.text())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add as report")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(keyword_row)
        layout.addWidget(self.keyword_hint)
        layout.addWidget(buttons)

    def _keyword_changed(self, words: str) -> None:
        words = words.strip()
        if not words or self.count_matches is None:
            self.add_keyword.setChecked(bool(words))
            self.keyword_hint.setText("" if words else "No keyword suggested: type a distinctive word, or leave it empty.")
            return
        matched, total = self.count_matches(words)
        common = total and matched / total > self.COMMON
        self.add_keyword.setChecked(not common)
        self.keyword_hint.setText(
            f"“{words}” would have matched {matched} of the last {total} radio messages."
            + ("  That is common: it would flag ordinary traffic. Choose a more distinctive word." if common else ""))

    def corrected(self, confidence: float) -> AnalysisResult:
        return AnalysisResult(True, self.category.currentText(), self.urgency.currentText(),
                              self.headline.text().strip() or "Report", max(confidence, 0.6))

    def chosen_keyword(self) -> str:
        return self.keyword.text().strip() if self.add_keyword.isChecked() else ""


class MissesView(QWidget):
    # key, Message, corrected AnalysisResult, place text, keyword
    add_requested = Signal(str, object, object, str, str)
    decisions_changed = Signal()
    count_changed = Signal(int)

    COLUMNS = ("Heard", "Source", "Model's headline", "Category", "Urgency", "Confidence", "Message")

    def __init__(self, store, format_time, parent=None, watch_hours=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.watch_hours = watch_hours or (lambda: 24)
        self._misses = []
        self.explain = QLabel(
            "Radio messages none of your watch forms kept, read in the background by your Ollama model; these are "
            "the ones it thinks are news. Add them as reports (correcting the model first), or dismiss them. "
            "Double-click for the full message; right-click for every option.")
        self.explain.setWordWrap(True)
        self.show_dismissed = QCheckBox("Show dismissed")
        self.show_dismissed.toggled.connect(self.refresh)
        self.status = QLabel()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((140, 110, 300, 150, 70, 80)):
            self.table.setColumnWidth(column, width)
        self.table.cellDoubleClicked.connect(lambda row, _column: self.show_message(row))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.add_button = QPushButton("Add as report…")
        self.add_button.clicked.connect(self._add)
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.setToolTip("Not news: hide it (Show dismissed to review or restore)")
        self.dismiss_button.clicked.connect(lambda: self._set_status("dismissed"))
        self.restore_button = QPushButton("Restore")
        self.restore_button.clicked.connect(lambda: self._set_status("new"))
        self.delete_button = QPushButton("Delete")
        self.delete_button.setToolTip("Remove it for good (it is not checked again)")
        self.delete_button.clicked.connect(self._delete)
        self._confirm_delete: list[str] | None = None
        top = QHBoxLayout()
        top.addWidget(self.explain, 1)
        top.addWidget(self.show_dismissed)
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        for button in (self.add_button, self.dismiss_button, self.restore_button, self.delete_button):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self._selection_changed()

    def refresh(self) -> None:
        since = datetime.now(timezone.utc) - timedelta(hours=self.watch_hours())
        status = "dismissed" if self.show_dismissed.isChecked() else "new"
        self._misses = [miss for miss in self.store.possible_misses(status, since)
                        if status != "new" or not is_fragment(miss[1].raw_content)]
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
                if column in (2, len(values) - 1):
                    item.setToolTip(value)
                self.table.setItem(row, column, item)
        dismissed = self.show_dismissed.isChecked()
        self.add_button.setVisible(True)
        self.dismiss_button.setVisible(not dismissed)
        self.restore_button.setVisible(dismissed)
        if not dismissed:
            self.count_changed.emit(len(self._misses))
        self._selection_changed()

    def show_progress(self, checked: int, waiting: int, error: str = "") -> None:
        counts = self.store.missed_counts()
        found = sum(counts.get(status, 0) for status in ("new", "added", "dismissed", "deleted"))
        text = (f"Checked {sum(counts.values())} messages so far ({checked} this session); {waiting} waiting. "
                f"Found {found}: {counts.get('added', 0)} added, {counts.get('dismissed', 0)} dismissed.")
        if error:
            text += f"  Ollama unavailable: {error}"
        self.status.setText(text)

    # -- selection and actions ---------------------------------------------------------

    def _selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        keys = [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in rows]
        return [miss for miss in self._misses if miss[0] in keys]

    def _selection_changed(self) -> None:
        chosen = bool(self.table.selectionModel() and self.table.selectionModel().selectedRows())
        for button in (self.add_button, self.dismiss_button, self.restore_button, self.delete_button):
            button.setEnabled(chosen)
        self.delete_button.setText("Delete")
        self._confirm_delete = None

    def _menu(self, position: QPoint) -> None:
        if not self._selected():
            return
        menu = QMenu(self)
        count = len(self._selected())
        menu.addAction("Add as report…").triggered.connect(self._add)
        if self.show_dismissed.isChecked():
            menu.addAction("Restore").triggered.connect(lambda: self._set_status("new"))
        else:
            menu.addAction("Dismiss (not news)").triggered.connect(lambda: self._set_status("dismissed"))
        menu.addAction("Delete").triggered.connect(lambda: self._delete(confirmed=True))
        if count == 1:
            menu.addSeparator()
            row = self.table.selectionModel().selectedRows()[0].row()
            menu.addAction("Show full message…").triggered.connect(lambda: self.show_message(row))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _add(self) -> None:
        for key, message, result in self._selected():
            dialog = AddMissDialog(message, result, self, count_matches=self.store.keyword_matches)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                continue
            self.add_requested.emit(key, message, dialog.corrected(result.confidence), dialog.place.text().strip(),
                                    dialog.chosen_keyword())
        self.decisions_changed.emit()
        self.refresh()

    def _set_status(self, status: str) -> None:
        for key, _message, _result in self._selected():
            self.store.set_missed_status(key, status)
        self.decisions_changed.emit()
        self.refresh()

    def _delete(self, confirmed: bool = False) -> None:
        keys = [key for key, _message, _result in self._selected()]
        if not keys:
            return
        if not confirmed and self._confirm_delete != keys:   # a second click confirms
            self._confirm_delete = keys
            self.delete_button.setText("Click again to delete")
            return
        for key in keys:
            self.store.set_missed_status(key, "deleted")   # kept as checked, so it isn't found again
        self.refresh()

    def show_message(self, row: int) -> None:
        item = self.table.item(row, 0)
        miss = next((m for m in self._misses if item is not None and m[0] == item.data(Qt.ItemDataRole.UserRole)), None)
        if miss is None:
            return
        _key, message, result = miss
        dialog = QDialog(self)
        dialog.setWindowTitle("Possible miss — full message")
        dialog.resize(680, 440)
        details = [f"Heard: {self.format_time(message.received_at)}   Source: {message.source_application}",
                   f"Model: {result.category}, {result.urgency} urgency, {result.confidence:.0%} — {result.summary}", "",
                   "Message:", readable_text(message)]
        if message.raw_content.strip().startswith("{") or message.fields:
            details += ["", "As received:", message.raw_content,
                        json.dumps(message.fields, indent=1) if message.fields else ""]
        text = QPlainTextEdit("\n".join(details))
        text.setReadOnly(True)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(buttons)
        dialog.exec()
