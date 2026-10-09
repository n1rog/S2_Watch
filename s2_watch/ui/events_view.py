"""The Events tab: reports grouped by the real-world happening they describe."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

PERIODS = (("24 hours", 24), ("48 hours", 48), ("7 days", 168), ("30 days", 720))
URGENCY_RANK = {"high": 3, "medium": 2, "low": 1}


class SortKeyItem(QTableWidgetItem):
    """A cell that sorts by a hidden key (times, counts) rather than its text."""

    def __init__(self, text: str, key) -> None:
        super().__init__(text)
        self.key = key

    def __lt__(self, other) -> bool:
        if isinstance(other, SortKeyItem):
            return self.key < other.key
        return super().__lt__(other)


class EventsView(QWidget):
    show_on_map = Signal(object)      # [(lat, lon), ...]
    draft_briefing = Signal()

    COLUMNS = ("Last activity", "Event", "Category", "Area", "Reports", "Sources", "Urgency", "Official",
               "Radio lead", "Labels")
    TIMELINE_COLUMNS = ("Heard", "Source", "From", "Urgency", "Report", "Official", "Label")

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.focus_regions: set[int] = set()
        self._events = []

        self.period = QComboBox()
        for label, hours in PERIODS:
            self.period.addItem(label, hours)
        saved = self.store.get_setting("events.period", "48")
        self.period.setCurrentIndex(max(0, self.period.findData(int(saved) if saved.isdigit() else 48)))
        self.period.currentIndexChanged.connect(self._period_changed)
        self.multi_only = QCheckBox("Only events with 2+ reports")
        self.multi_only.setChecked(self.store.get_setting("events.multi_only", "0") == "1")
        self.multi_only.toggled.connect(self._filter_changed)
        self.focus_only = QCheckBox("Focus regions only")
        self.focus_only.setChecked(self.store.get_setting("events.focus_only", "0") == "1")
        self.focus_only.toggled.connect(self._filter_changed)
        self.count = QLabel()
        briefing = QPushButton("Draft briefing…")
        briefing.setToolTip("Draft a shift briefing from these events (full report, FLMSG and JS8Call versions)")
        briefing.clicked.connect(self.draft_briefing.emit)
        top = QHBoxLayout()
        top.addWidget(QLabel("Period:"))
        top.addWidget(self.period)
        top.addSpacing(12)
        top.addWidget(self.multi_only)
        top.addWidget(self.focus_only)
        top.addStretch(1)
        top.addWidget(self.count)
        top.addWidget(briefing)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)   # the event's title gets the room
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._show_selected())

        self.heading = QLabel("Select an event to see its timeline.")
        self.map_button = QPushButton("Show on map")
        self.map_button.setEnabled(False)
        self.map_button.clicked.connect(self._show_selected)
        detail_top = QHBoxLayout()
        detail_top.addWidget(self.heading, 1)
        detail_top.addWidget(self.map_button)
        self.timeline = QTableWidget(0, len(self.TIMELINE_COLUMNS))
        self.timeline.setHorizontalHeaderLabels(self.TIMELINE_COLUMNS)
        self.timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline.setWordWrap(False)
        self.timeline.horizontalHeader().setStretchLastSection(True)
        self.timeline.verticalHeader().setVisible(False)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addLayout(detail_top)
        detail_layout.addWidget(self.timeline)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(detail)
        splitter.setSizes([400, 250])
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter)

        # New reports arrive in bursts; refresh once they settle.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self.refresh)

    # -- data --------------------------------------------------------------------------

    def data_changed(self) -> None:
        self._refresh_timer.start()

    def set_focus_regions(self, regions) -> None:
        self.focus_regions = set(regions)
        self.focus_only.setEnabled(bool(self.focus_regions))
        self.refresh()

    def period_hours(self) -> int:
        return int(self.period.currentData())

    def visible_events(self):
        since = datetime.now(timezone.utc) - timedelta(hours=self.period_hours())
        regions = self.focus_regions if self.focus_only.isChecked() and self.focus_regions else None
        events = self.store.events_since(since, regions)
        if self.multi_only.isChecked():
            events = [event for event in events if event.reports > 1]
        return events

    def refresh(self) -> None:
        selected = self.selected_event()
        self._events = self.visible_events()
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for event in self._events:
            row = self.table.rowCount()
            self.table.insertRow(row)
            area = ", ".join(part for part in (event.state, f"R{event.region}" if event.region else "") if part)
            lead = f"{event.radio_lead_minutes} min" if event.radio_lead_minutes else ""
            cells = (
                SortKeyItem(self.format_time(event.last_seen), event.last_seen.timestamp()),
                QTableWidgetItem(event.title),
                QTableWidgetItem(event.category),
                QTableWidgetItem(area),
                SortKeyItem(str(event.reports), event.reports),
                QTableWidgetItem(", ".join(event.sources)),
                SortKeyItem(event.urgency, URGENCY_RANK.get(event.urgency, 0)),
                QTableWidgetItem(f"✓ {event.official}" if event.official else ""),
                SortKeyItem(lead, event.radio_lead_minutes or 0),
                QTableWidgetItem(", ".join(status for status in event.statuses if status)),
            )
            cells[0].setData(Qt.ItemDataRole.UserRole, event.event_id)
            if event.reports > 1:
                font = QFont(cells[1].font())
                font.setBold(True)
                cells[1].setFont(font)
            cells[1].setToolTip(event.title)
            for column, item in enumerate(cells):
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(sorting)
        multi = sum(event.reports > 1 for event in self._events)
        self.count.setText(f"{len(self._events)} events, {multi} with several reports")
        if selected is not None:
            self.select_event(selected)

    # -- selection ---------------------------------------------------------------------

    def selected_event(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select_event(self, event_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == event_id:
                self.table.selectRow(row)
                return

    def _event(self, event_id):
        return next((event for event in self._events if event.event_id == event_id), None)

    def _selection_changed(self) -> None:
        event = self._event(self.selected_event())
        self.timeline.setRowCount(0)
        self.map_button.setEnabled(bool(event and event.points))
        if event is None:
            self.heading.setText("Select an event to see its timeline.")
            return
        self.heading.setText(f"<b>{event.title}</b> — {event.reports} report{'s' if event.reports != 1 else ''}, "
                             f"{self.format_time(event.first_seen)} to {self.format_time(event.last_seen)}")
        for report in self.store.event_reports(event.event_id):
            row = self.timeline.rowCount()
            self.timeline.insertRow(row)
            text = report["summary"] or " ".join(report["text"].split())
            copies = f" ({report['copies']} copies)" if report["copies"] > 1 else ""
            values = (self.format_time(report["first_seen"]), report["source"] + copies, report["sender"],
                      report["urgency"], text, report["corroboration"], report["status"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value or "")
                if column == 4:
                    item.setToolTip(" ".join(report["text"].split()))
                self.timeline.setItem(row, column, item)
        self.timeline.resizeColumnsToContents()

    def _show_selected(self) -> None:
        event = self._event(self.selected_event())
        if event and event.points:
            self.show_on_map.emit(list(event.points))

    def _period_changed(self) -> None:
        self.store.set_setting("events.period", str(self.period_hours()))
        self.refresh()

    def _filter_changed(self) -> None:
        self.store.set_setting("events.multi_only", "1" if self.multi_only.isChecked() else "0")
        self.store.set_setting("events.focus_only", "1" if self.focus_only.isChecked() else "0")
        self.refresh()
