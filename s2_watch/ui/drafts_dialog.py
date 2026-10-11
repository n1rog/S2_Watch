"""Reports → Saved Reports & Briefings: the drafts kept in S2 Watch, to reopen or delete."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

KIND_LABELS = {"briefing": "Briefing", "report": "Report"}


class DraftsDialog(QDialog):
    open_requested = Signal(int)   # draft id

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.setWindowTitle("Saved Reports & Briefings")
        self.resize(820, 460)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Saved", "Type", "DTG", "Subject"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._open())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.empty = QLabel("Nothing saved yet. Use “Save in S2 Watch” in a briefing or report draft.")
        buttons = QDialogButtonBox()
        self.open_button = buttons.addButton("Open", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_button.clicked.connect(self._open)
        self.delete_button = buttons.addButton("Delete", QDialogButtonBox.ButtonRole.ActionRole)
        self.delete_button.clicked.connect(self._delete)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.empty)
        layout.addWidget(self.table)
        layout.addWidget(buttons)
        self._confirm_delete = None
        self.refresh()

    def refresh(self) -> None:
        drafts = self.store.list_drafts()
        self.table.setRowCount(0)
        for draft in drafts:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (self.format_time(draft["updated_at"]), KIND_LABELS.get(draft["kind"], draft["kind"]),
                      draft["dtg"], draft["subject"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, draft["id"])
                self.table.setItem(row, column, item)
        self.empty.setVisible(not drafts)
        self._selection_changed()

    def selected_draft(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None

    def _selection_changed(self) -> None:
        chosen = self.selected_draft() is not None
        self.open_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)
        self.delete_button.setText("Delete")
        self._confirm_delete = None

    def _open(self) -> None:
        draft_id = self.selected_draft()
        if draft_id is not None:
            self.accept()
            self.open_requested.emit(draft_id)

    def _delete(self) -> None:
        draft_id = self.selected_draft()
        if draft_id is None:
            return
        if self._confirm_delete != draft_id:   # a second click confirms
            self._confirm_delete = draft_id
            self.delete_button.setText("Click again to delete")
            return
        self.store.delete_draft(draft_id)
        self.refresh()
