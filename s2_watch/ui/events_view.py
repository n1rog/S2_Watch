"""Events: reports (radio and official) grouped by the happening they describe.

``EventsTable`` is the list itself, used under the map (switchable with the Reports list)
and in the Events tab, which adds its own period, filters and each event's timeline.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

PERIODS = (("24 hours", 24), ("48 hours", 48), ("7 days", 168), ("30 days", 720))
URGENCY_RANK = {"high": 3, "medium": 2, "low": 1}
PINNED_STATUSES = {"interest", "followup"}
HIGHLIGHT_SECONDS = 15


class SortKeyItem(QTableWidgetItem):
    """A cell that sorts by a hidden key (times, counts) rather than its text."""

    def __init__(self, text: str, key) -> None:
        super().__init__(text)
        self.key = key

    def __lt__(self, other) -> bool:
        if isinstance(other, SortKeyItem):
            return self.key < other.key
        return super().__lt__(other)


class EventsTable(QTableWidget):
    """A list of events; right-click and double-click are handled by the window."""

    menu_requested = Signal(object, QPoint)   # [event ids], global position
    activated = Signal(int)                   # event id (double-click)

    COLUMNS = ("Last activity", "Event", "Category", "Area", "Reports", "Sources", "Urgency", "Official",
               "Radio lead", "Labels")
    EVENT_ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, format_time, parent=None) -> None:
        super().__init__(0, len(self.COLUMNS), parent)
        self.format_time = format_time
        self.events = []
        self._seen: dict[int, datetime] = {}       # event id -> last activity at the previous fill
        self._highlight_until: dict[int, float] = {}
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)   # the event's title gets the room
        self.verticalHeader().setVisible(False)
        self.setSortingEnabled(True)
        self.sortItems(0, Qt.SortOrder.DescendingOrder)   # focus regions, then the latest activity first
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda position: self.selected_events() and self.menu_requested.emit(
                self.selected_events(), self.viewport().mapToGlobal(position)))
        self.cellDoubleClicked.connect(lambda row, _column: self._activate(row))
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setInterval(5000)
        self._highlight_timer.timeout.connect(self._fade_highlights)

    def set_events(self, events, focus: set[int] | None = None, limit: int | None = None) -> None:
        """Show events: focus regions first, then the latest activity. With ``limit``, rows past
        it are hidden, except events labelled Of interest or Follow-up."""
        focus = set(focus or ())
        selected = set(self.selected_events())
        first_fill = not self._seen
        now = time.monotonic()
        for event in events:
            previous = self._seen.get(event.event_id)
            if not first_fill and (previous is None or event.last_seen > previous):
                self._highlight_until[event.event_id] = now + HIGHLIGHT_SECONDS   # new, or a new report
        self._seen = {event.event_id: event.last_seen for event in events}
        self.events = list(events)
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.setRowCount(0)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        for event in events:
            row = self.rowCount()
            self.insertRow(row)
            area = ", ".join(part for part in (event.state, f"R{event.region}" if event.region else "") if part)
            lead = f"{event.radio_lead_minutes} min" if event.radio_lead_minutes else ""
            in_focus = bool(focus) and event.region in focus
            cells = (
                SortKeyItem(self.format_time(event.last_seen), (in_focus, event.last_seen.timestamp())),
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
            cells[0].setData(self.EVENT_ROLE, event.event_id)
            cells[1].setToolTip(event.title)
            ignored = bool(event.statuses) and set(event.statuses) == {"ignored"}
            for column, item in enumerate(cells):
                font = QFont(item.font())
                font.setBold(column == 1 and event.reports > 1 or column == 9 and bool(PINNED_STATUSES & set(event.statuses)))
                font.setItalic(ignored)
                item.setFont(font)
                if ignored:
                    item.setForeground(QBrush(muted))
                self.setItem(row, column, item)
        self.setSortingEnabled(sorting)
        if sorting:
            header = self.horizontalHeader()
            self.sortItems(header.sortIndicatorSection(), header.sortIndicatorOrder())
        self.apply_limit(limit)
        self._paint_highlights()
        for event_id in selected:
            self.select_event(event_id, clear=False)

    def apply_limit(self, limit: int | None) -> None:
        events = {event.event_id: event for event in self.events}
        for row in range(self.rowCount()):
            event = events.get(self.item(row, 0).data(self.EVENT_ROLE))
            pinned = event is not None and bool(PINNED_STATUSES & set(event.statuses))
            self.setRowHidden(row, limit is not None and row >= limit and not pinned)

    def _paint_highlights(self) -> None:
        now = time.monotonic()
        tint = QColor(self.palette().color(QPalette.ColorRole.Highlight))
        tint.setAlpha(55)
        active = False
        for row in range(self.rowCount()):
            event_id = self.item(row, 0).data(self.EVENT_ROLE)
            fresh = self._highlight_until.get(event_id, 0) > now
            active = active or fresh
            for column in range(self.columnCount()):
                self.item(row, column).setBackground(QBrush(tint) if fresh else QBrush())
        if active and not self._highlight_timer.isActive():
            self._highlight_timer.start()
        elif not active:
            self._highlight_timer.stop()

    def _fade_highlights(self) -> None:
        now = time.monotonic()
        self._highlight_until = {event_id: until for event_id, until in self._highlight_until.items() if until > now}
        self._paint_highlights()

    def selected_events(self) -> list[int]:
        rows = sorted({index.row() for index in self.selectionModel().selectedRows()}) if self.selectionModel() else []
        return [self.item(row, 0).data(self.EVENT_ROLE) for row in rows if self.item(row, 0) is not None]

    def event_by_id(self, event_id):
        return next((event for event in self.events if event.event_id == event_id), None)

    def select_event(self, event_id: int, clear: bool = True) -> bool:
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is not None and item.data(self.EVENT_ROLE) == event_id:
                if clear:
                    self.clearSelection()
                self.setRowHidden(row, False)
                self.selectRow(row)
                self.scrollToItem(item)
                return True
        return False

    def _activate(self, row: int) -> None:
        item = self.item(row, 0)
        if item is not None:
            self.activated.emit(item.data(self.EVENT_ROLE))


class EventsView(QWidget):
    """The Events tab: the list with its own period and filters, and each event's timeline."""

    show_on_map = Signal(object)      # [(lat, lon), ...]
    draft_briefing = Signal()

    TIMELINE_COLUMNS = ("Heard", "Source", "From", "Urgency", "Report", "Official", "Label")

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.focus_regions: set[int] = set()

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

        self.table = EventsTable(format_time)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.activated.connect(lambda event_id: self._show(event_id))

        self.heading = QLabel("Select an event to see its timeline.")
        self.map_button = QPushButton("Show on map")
        self.map_button.setEnabled(False)
        self.map_button.clicked.connect(lambda: self._show(self.selected_event()))
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
        events = self.visible_events()
        self.table.set_events(events, self.focus_regions)
        multi = sum(event.reports > 1 for event in events)
        self.count.setText(f"{len(events)} events, {multi} with several reports")
        self._selection_changed()

    def selected_event(self) -> int | None:
        selected = self.table.selected_events()
        return selected[0] if selected else None

    def _selection_changed(self) -> None:
        event = self.table.event_by_id(self.selected_event()) if self.selected_event() is not None else None
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

    def _show(self, event_id) -> None:
        event = self.table.event_by_id(event_id) if event_id is not None else None
        if event and event.points:
            self.show_on_map.emit(list(event.points))

    def _period_changed(self) -> None:
        self.store.set_setting("events.period", str(self.period_hours()))
        self.refresh()

    def _filter_changed(self) -> None:
        self.store.set_setting("events.multi_only", "1" if self.multi_only.isChecked() else "0")
        self.store.set_setting("events.focus_only", "1" if self.focus_only.isChecked() else "0")
        self.refresh()
