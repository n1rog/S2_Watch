"""Reports → Sender Track Records: how each radio station's reports have held up."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from s2_watch.senders import GRADE_WORDS, MIN_JUDGED
from s2_watch.ui.events_view import SortKeyItem

GRADE_ORDER = {"B": 5, "C": 4, "D": 3, "E": 2, "F": 1}


class SendersDialog(QDialog):
    COLUMNS = ("Call sign", "Reports", "Confirmed", "Marked useful", "Ignored", "Suggested grade", "Last heard")

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sender Track Records")
        self.resize(900, 560)
        intro = QLabel(
            "How each station's newsworthy radio reports have held up. A report counts for the station when an "
            "official source confirmed its event or you marked it Of interest or Follow-up, and against it when "
            f"you marked it Ignored. With {MIN_JUDGED} or more judged reports, the share that held up suggests a "
            "reliability grade (B usually reliable, C fairly reliable, D not usually reliable, E unreliable); "
            "fewer is F, cannot be judged. MAGNET reports use the grade in their source ratings. "
            "Exercise and test traffic doesn't count.")
        intro.setWordWrap(True)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        records = sorted(store.sender_track_records().values(), key=lambda r: (-r.judged, -r.reports, r.callsign))
        for record in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            grade = SortKeyItem(f"{record.grade} — {GRADE_WORDS[record.grade]}", (GRADE_ORDER[record.grade], record.judged))
            grade.setToolTip(record.explanation)
            cells = (QTableWidgetItem(record.callsign), SortKeyItem(str(record.reports), record.reports),
                     SortKeyItem(str(record.confirmed), record.confirmed), SortKeyItem(str(record.useful), record.useful),
                     SortKeyItem(str(record.ignored), record.ignored), grade,
                     SortKeyItem(format_time(record.last_heard) if record.last_heard else "",
                                 record.last_heard.timestamp() if record.last_heard else 0))
            for column, item in enumerate(cells):
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter if column < 5 else Qt.AlignmentFlag.AlignLeft
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(5, Qt.SortOrder.DescendingOrder)     # graded stations first
        empty = QLabel("No radio reports from identified stations yet.")
        empty.setVisible(not records)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(empty)
        layout.addWidget(self.table)
        layout.addWidget(buttons)
