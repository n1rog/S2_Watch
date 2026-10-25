"""Reports → Sender Track Records: how each radio station's reports have held up, and the
trust level the operator sets for a station."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu,
    QPushButton, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from s2_watch.senders import GRADE_WORDS, MIN_JUDGED, TRUST_LEVELS, TrackRecord
from s2_watch.ui.events_view import SortKeyItem

GRADE_ORDER = {"A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
AUTOMATIC = "Automatic"


def trust_label(grade: str) -> str:
    return f"{grade} — {GRADE_WORDS[grade]}" if grade else AUTOMATIC


class TrustDelegate(QStyledItemDelegate):
    """The Trust level column: a list of levels to pick from."""

    def __init__(self, dialog: "SendersDialog") -> None:
        super().__init__(dialog.table)
        self.dialog = dialog

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.addItem(f"{AUTOMATIC} (the suggested grade)", "")
        for grade in TRUST_LEVELS:
            editor.addItem(trust_label(grade), grade)
        editor.activated.connect(lambda _index, editor=editor: self.commitData.emit(editor))
        editor.activated.connect(lambda _index, editor=editor: self.closeEditor.emit(editor))
        return editor

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentIndex(max(0, editor.findData(index.data(Qt.ItemDataRole.UserRole) or "")))
        editor.showPopup()

    def setModelData(self, editor, model, index) -> None:
        station = model.index(index.row(), 0).data()
        self.dialog.set_trust([station], editor.currentData())


class SendersDialog(QDialog):
    COLUMNS = ("Call sign", "Reports", "Confirmed", "Marked useful", "Ignored", "Suggested grade", "Trust level",
               "Last heard")
    TRUST_COLUMN = 6

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.setWindowTitle("Sender Track Records")
        self.resize(1000, 580)
        intro = QLabel(
            "How each station's newsworthy radio reports have held up. A report counts for the station when an "
            "official source confirmed its event or you marked it Of interest or Follow-up, and against it when "
            f"you marked it Ignored. With {MIN_JUDGED} or more judged reports, the share that held up suggests a "
            "reliability grade (B usually reliable, C fairly reliable, D not usually reliable, E unreliable); "
            "fewer is F, cannot be judged. Exercise and test traffic doesn't count.<br><br>"
            "<b>Trust level</b> is yours to set from what you know of a station: click its cell (or right-click "
            "selected stations) and choose A completely reliable to F cannot be judged. It replaces the suggested "
            "grade in MAGNET source ratings and the event details; <b>Automatic</b> goes back to the suggestion.")
        intro.setWordWrap(True)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.setItemDelegateForColumn(self.TRUST_COLUMN, TrustDelegate(self))
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.empty = QLabel("No radio reports from identified stations yet. Add station… sets a trust level ahead.")
        add = QPushButton("Add station…")
        add.setToolTip("Set the trust level of a station that hasn't sent a report yet")
        add.clicked.connect(self.add_station)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(add)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.empty)
        layout.addWidget(self.table)
        layout.addLayout(bottom)
        self.refresh()
        self.table.sortItems(5, Qt.SortOrder.DescendingOrder)     # graded stations first

    def refresh(self) -> None:
        records = sorted(self.store.sender_track_records().values(), key=lambda r: (-r.judged, -r.reports, r.callsign))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for record in records:
            self.add_row(record)
        self.table.setSortingEnabled(True)
        self.empty.setVisible(not records)

    def add_row(self, record: TrackRecord) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        suggested = SortKeyItem(f"{record.suggested} — {GRADE_WORDS[record.suggested]}",
                                (GRADE_ORDER[record.suggested], record.judged))
        suggested.setToolTip(record.basis)
        trust = SortKeyItem(trust_label(record.trust), GRADE_ORDER.get(record.trust, 0))
        trust.setData(Qt.ItemDataRole.UserRole, record.trust)
        trust.setToolTip("Click to set this station's trust level")
        if record.trust:
            font = trust.font()
            font.setBold(True)
            trust.setFont(font)
        cells = (QTableWidgetItem(record.callsign), SortKeyItem(str(record.reports), record.reports),
                 SortKeyItem(str(record.confirmed), record.confirmed), SortKeyItem(str(record.useful), record.useful),
                 SortKeyItem(str(record.ignored), record.ignored), suggested, trust,
                 SortKeyItem(self.format_time(record.last_heard) if record.last_heard else "",
                             record.last_heard.timestamp() if record.last_heard else 0))
        for column, item in enumerate(cells):
            if column != self.TRUST_COLUMN:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if column:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter if column < 5 else Qt.AlignmentFlag.AlignLeft
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, column, item)

    def selected_stations(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.table.item(row, 0).text() for row in rows]

    def set_trust(self, stations, grade: str) -> None:
        for station in stations:
            self.store.set_sender_trust(station, grade)
        QTimer.singleShot(0, self.refresh)      # after the level list has closed

    def show_menu(self, position) -> None:
        stations = self.selected_stations()
        if not stations:
            return
        menu = QMenu(self)
        menu.addSection(f"Trust level for {stations[0]}" if len(stations) == 1 else f"Trust level for {len(stations)} stations")
        for grade in ("", *TRUST_LEVELS):
            action = menu.addAction(trust_label(grade))
            action.triggered.connect(lambda _checked=False, grade=grade: self.set_trust(stations, grade))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def add_station(self) -> None:
        station, ok = QInputDialog.getText(self, "Add station", "Call sign:")
        station = station.strip().upper()
        if not ok or not station:
            return
        levels = [trust_label(grade) for grade in TRUST_LEVELS]
        level, ok = QInputDialog.getItem(self, "Add station", f"Trust level for {station}:", levels, 1, False)
        if ok:
            self.set_trust([station], TRUST_LEVELS[levels.index(level)])
