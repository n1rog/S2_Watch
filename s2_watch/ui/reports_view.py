"""The Reports tab: every follow-up and briefing drafted in S2 Watch, with its status, its event
and the files written from it, to reopen, open its files, copy for radio or mark sent."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from s2_watch.followup import flmsg_text
from s2_watch.ui.events_view import search_box

KIND_LABELS = {"briefing": "Briefing", "report": "Report"}


def draft_status(draft: dict) -> str:
    if draft["sent_at"]:
        return "Sent"
    return "Scheduled draft" if draft["origin"] == "scheduled" else "Draft"


class ReportsView(QWidget):
    open_requested = Signal(int)           # draft id
    event_requested = Signal(int)          # event id
    draft_briefing = Signal()
    changed = Signal()                     # sent / deleted

    def __init__(self, store, format_time, js8=None, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.js8 = js8                       # full report -> JS8Call message
        self.drafts: list[dict] = []
        self.kind = QComboBox()
        for label, value in (("All types", ""), ("Briefings", "briefing"), ("Reports", "report")):
            self.kind.addItem(label, value)
        self.status = QComboBox()
        for label in ("Any status", "Draft", "Scheduled draft", "Sent"):
            self.status.addItem(label, "" if label == "Any status" else label)
        self.kind.currentIndexChanged.connect(self._show)
        self.status.currentIndexChanged.connect(self._show)
        self.search = search_box("Search subjects and text", lambda _text: self._show())
        self.count = QLabel()
        briefing = QPushButton("Draft briefing…")
        briefing.clicked.connect(self.draft_briefing.emit)
        top = QHBoxLayout()
        top.addWidget(self.kind)
        top.addWidget(self.status)
        top.addWidget(self.search)
        top.addWidget(self.count)
        top.addStretch(1)
        top.addWidget(briefing)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("Updated", "Type", "DTG", "Subject", "Status", "Event", "Files"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self._open())
        self.empty = QLabel("No reports yet. Draft one from an event (Draft follow-up…) or with Draft briefing…; "
                            "Save keeps it here and writes its files to the reports folder.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: gray; padding: 12px")

        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self._open)
        self.word_button = QPushButton("Open Word file")
        self.word_button.clicked.connect(lambda: self._open_file(".docx"))
        self.web_button = QPushButton("Open web page")
        self.web_button.clicked.connect(lambda: self._open_file(".html"))
        self.folder_button = QPushButton("Show in folder")
        self.folder_button.clicked.connect(self._show_folder)
        self.copy_button = QToolButton()
        self.copy_button.setText("Copy for radio")
        self.copy_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        copy_menu = QMenu(self.copy_button)
        copy_menu.addAction("FLMSG text").triggered.connect(lambda: self._copy("flmsg"))
        copy_menu.addAction("JS8Call message").triggered.connect(lambda: self._copy("js8"))
        self.copy_button.setMenu(copy_menu)
        self.sent_button = QPushButton("Mark sent")
        self.sent_button.clicked.connect(self._toggle_sent)
        self.event_button = QPushButton("Open event")
        self.event_button.clicked.connect(self._open_event)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setToolTip("Delete the draft from S2 Watch (its files stay in the reports folder)")
        self.delete_button.clicked.connect(self._delete)
        self._confirm_delete: int | None = None
        actions = QHBoxLayout()
        for button in (self.open_button, self.word_button, self.web_button, self.folder_button, self.copy_button,
                       self.sent_button, self.event_button, self.delete_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        self.drafts = self.store.list_drafts()
        ids = {draft["event_id"] for draft in self.drafts if draft["event_id"] is not None}
        self.event_titles = {event.event_id: event.title
                             for event in self.store.events_since(None, event_ids=ids)} if ids else {}
        self._show()

    def shown(self) -> list[dict]:
        kind, status = self.kind.currentData(), self.status.currentData()
        words = self.search.text().lower().split()
        result = []
        for draft in self.drafts:
            if (kind and draft["kind"] != kind) or (status and draft_status(draft) != status):
                continue
            if words:
                text = (draft["subject"] + " " + (self.store.get_draft(draft["id"]) or {}).get("text", "")).lower()
                if not all(word in text for word in words):
                    continue
            result.append(draft)
        return result

    def _show(self) -> None:
        selected = self.selected()
        shown = self.shown()
        self.table.setRowCount(0)
        for draft in shown:
            row = self.table.rowCount()
            self.table.insertRow(row)
            event = self.event_titles.get(draft["event_id"], "") if draft["event_id"] is not None else ""
            files = draft["files"]
            values = (self.format_time(datetime.fromisoformat(draft["updated_at"])), KIND_LABELS.get(draft["kind"], ""),
                      draft["dtg"], draft["subject"], draft_status(draft), event,
                      f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "not saved to files")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip("\n".join(files) if column == 6 and files else value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, draft["id"])
                self.table.setItem(row, column, item)
            if selected is not None and draft["id"] == selected["id"]:
                self.table.selectRow(row)
        self.count.setText(f"{len(shown)} of {len(self.drafts)}")
        self.empty.setVisible(not self.drafts)
        self._selection_changed()

    def selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        draft_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((draft for draft in self.drafts if draft["id"] == draft_id), None)

    def select_draft(self, draft_id: int) -> bool:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == draft_id:
                self.table.selectRow(row)
                return True
        return False

    def _file(self, draft: dict | None, suffix: str) -> Path | None:
        if draft is None:
            return None
        return next((Path(path) for path in reversed(draft["files"])
                     if path.endswith(suffix) and Path(path).exists()), None)

    def _selection_changed(self) -> None:
        draft = self.selected()
        for button in (self.open_button, self.copy_button, self.sent_button, self.delete_button):
            button.setEnabled(draft is not None)
        self.word_button.setEnabled(self._file(draft, ".docx") is not None)
        self.web_button.setEnabled(self._file(draft, ".html") is not None)
        self.folder_button.setEnabled(draft is not None and any(Path(path).parent.exists() for path in draft["files"]))
        self.event_button.setEnabled(draft is not None and draft["event_id"] is not None)
        self.sent_button.setText("Mark not sent" if draft is not None and draft["sent_at"] else "Mark sent")
        self.delete_button.setText("Delete")
        self._confirm_delete = None

    def _open(self) -> None:
        draft = self.selected()
        if draft is not None:
            self.open_requested.emit(draft["id"])

    def _open_file(self, suffix: str) -> None:
        path = self._file(self.selected(), suffix)
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_folder(self) -> None:
        draft = self.selected()
        folder = next((Path(path).parent for path in reversed(draft["files"]) if Path(path).parent.exists()), None) \
            if draft else None
        if folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _copy(self, kind: str) -> None:
        draft = self.selected()
        if draft is None:
            return
        text = (self.store.get_draft(draft["id"]) or {}).get("text", "")
        QGuiApplication.clipboard().setText(flmsg_text(text) if kind == "flmsg" or self.js8 is None else self.js8(text))

    def _toggle_sent(self) -> None:
        draft = self.selected()
        if draft is not None:
            self.store.mark_draft_sent(draft["id"], not draft["sent_at"])
            self.refresh()
            self.changed.emit()

    def _open_event(self) -> None:
        draft = self.selected()
        if draft is not None and draft["event_id"] is not None:
            self.event_requested.emit(draft["event_id"])

    def _delete(self) -> None:
        draft = self.selected()
        if draft is None:
            return
        if self._confirm_delete != draft["id"]:          # a second click confirms
            self._confirm_delete = draft["id"]
            self.delete_button.setText("Click again to delete")
            return
        self.store.delete_draft(draft["id"])
        self.refresh()
        self.changed.emit()
