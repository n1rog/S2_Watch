"""The Needs Review tab: what is waiting on the operator (s2_watch.review), with filter chips,
and beside it the situation now and the recent notices."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QListWidget, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from s2_watch.review import KINDS


class ReviewView(QWidget):
    act = Signal(object, str)          # a ReviewItem, "primary" or "secondary"
    draft_briefing = Signal()
    count_changed = Signal(int)

    def __init__(self, items_for, situation_for, format_time, parent=None) -> None:
        """``items_for()``: the ReviewItems waiting; ``situation_for()``: HTML for the
        situation panel."""
        super().__init__(parent)
        self.items_for = items_for
        self.situation_for = situation_for
        self.format_time = format_time
        self.items: list = []
        self.chips: dict[str, QToolButton] = {}
        chips = QHBoxLayout()
        chips.addWidget(QLabel("Show:"))
        for kind, (label, _primary, _secondary) in KINDS.items():
            chip = QToolButton()
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setProperty("label", label)
            chip.toggled.connect(self._show)
            chips.addWidget(chip)
            self.chips[kind] = chip
        chips.addStretch(1)
        briefing = QPushButton("Draft briefing…")
        briefing.clicked.connect(self.draft_briefing.emit)
        chips.addWidget(briefing)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("When", "Needs", "Item", "Detail"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self._act("primary"))
        self.empty = QLabel("Nothing needs you right now. New high-urgency events, AI assessments, scheduled briefings, "
                            "single-source reports, possible misses and unsent drafts appear here.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: gray; padding: 12px")
        self.primary = QPushButton("Open")
        self.primary.clicked.connect(lambda: self._act("primary"))
        self.secondary = QPushButton("Hide")
        self.secondary.setToolTip("Take it off this list (it stays everywhere else)")
        self.secondary.clicked.connect(lambda: self._act("secondary"))
        actions = QHBoxLayout()
        actions.addWidget(self.primary)
        actions.addWidget(self.secondary)
        actions.addStretch(1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(chips)
        left_layout.addWidget(self.empty)
        left_layout.addWidget(self.table, 1)
        left_layout.addLayout(actions)

        self.situation = QLabel()
        self.situation.setTextFormat(Qt.TextFormat.RichText)
        self.situation.setWordWrap(True)
        self.situation.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.notices = QListWidget()
        self.notices.setWordWrap(True)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("<b>Situation now</b>"))
        right_layout.addWidget(self.situation)
        right_layout.addWidget(QLabel("<b>Recent notices</b>"))
        right_layout.addWidget(self.notices, 1)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._timer = QTimer(self)        # reports arrive in bursts: refresh once they settle
        self._timer.setSingleShot(True)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh)
        self._selection_changed()

    def data_changed(self) -> None:
        self._timer.start()

    def refresh(self) -> None:
        self.items = self.items_for()
        counts = {kind: 0 for kind in KINDS}
        for item in self.items:
            counts[item.kind] += 1
        for kind, chip in self.chips.items():
            chip.setText(f"{chip.property('label')} ({counts[kind]})")
        self.situation.setText(self.situation_for())
        self._show()
        self.count_changed.emit(len(self.items))

    def shown(self) -> list:
        return [item for item in self.items if self.chips[item.kind].isChecked()]

    def _show(self, *_args) -> None:
        selected = self.selected()
        self.table.setRowCount(0)
        for item in self.shown():
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate((self.format_time(item.when), item.label, item.title, item.detail)):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.key)
                self.table.setItem(row, column, cell)
            if selected is not None and item.key == selected.key:
                self.table.selectRow(row)
        self.empty.setVisible(not self.table.rowCount())
        self.table.setVisible(bool(self.table.rowCount()))
        self._selection_changed()

    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        key = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((item for item in self.items if item.key == key), None)

    def _selection_changed(self) -> None:
        item = self.selected()
        self.primary.setEnabled(item is not None)
        self.secondary.setEnabled(item is not None)
        _label, primary, secondary = KINDS[item.kind] if item is not None else ("", "Open", "Hide")
        self.primary.setText(primary)
        self.secondary.setText(secondary)

    def _act(self, which: str) -> None:
        item = self.selected()
        if item is not None:
            self.act.emit(item, which)

    def set_notices(self, notices, format_time) -> None:
        self.notices.clear()
        for when, text, *_event in notices:
            self.notices.addItem(f"{format_time(when)}  {text}")
