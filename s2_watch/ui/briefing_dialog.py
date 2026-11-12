"""Before drafting a briefing: what period it covers and which events go in it."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
    QVBoxLayout,
)

from s2_watch.briefing_periods import PERIODS, describe, period_start

URGENCY_WORDS = {"high": "HIGH", "medium": "medium", "low": "low"}


class BriefingPeriodDialog(QDialog):
    """Pick the period (since the last briefing by default) and tick the events to include.
    ``events_for(key, start)`` gives the events for a period (start None: the Events tab's)."""

    def __init__(self, store, events_for, format_time=None, parent=None, now: datetime | None = None,
                 concern_of=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Draft MAGNET S2 briefing")
        self.resize(620, 520)
        self.store = store
        self.events_for = events_for
        self.format_time = format_time
        self.concern_of = concern_of     # event -> its Concern: shown, and the most concerning first
        self.now = now or datetime.now(timezone.utc)
        self.start: datetime | None = None
        self.events: list = []

        self.period = QComboBox()
        for key, label in PERIODS:
            self.period.addItem(label, key)
        saved = store.get_setting("briefing.period", "since_last")
        self.period.setCurrentIndex(max(0, self.period.findData(saved)))
        self.hours = QSpinBox()
        self.hours.setRange(1, 720)
        self.hours.setSuffix(" h")
        self.hours.setValue(int(store.get_setting("briefing.custom_hours", "6") or 6))
        self.period.currentIndexChanged.connect(self._load)
        self.hours.valueChanged.connect(self._load)
        row = QHBoxLayout()
        row.addWidget(QLabel("Period:"))
        row.addWidget(self.period, 1)
        row.addWidget(self.hours)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._count)
        every = QPushButton("Tick all")
        every.clicked.connect(lambda: self._tick(True))
        none = QPushButton("Untick all")
        none.clicked.connect(lambda: self._tick(False))
        ticks = QHBoxLayout()
        ticks.addWidget(every)
        ticks.addWidget(none)
        ticks.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.draft = buttons.addButton("Draft briefing", QDialogButtonBox.ButtonRole.AcceptRole)
        self.draft.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.summary)
        layout.addWidget(QLabel("Events in the briefing (untick any to leave out):"))
        layout.addWidget(self.list, 1)
        layout.addLayout(ticks)
        layout.addWidget(buttons)
        self._load()

    def key(self) -> str:
        return self.period.currentData()

    def _load(self) -> None:
        key = self.key()
        self.hours.setVisible(key == "custom")
        self.start = None if key == "events_tab" else period_start(key, self.now, self.store, self.hours.value())
        events = self.events_for(key, self.start)
        levels = {event.event_id: self.concern_of(event) for event in events} if self.concern_of else {}
        if levels:
            events = sorted(events, key=lambda event: levels[event.event_id].rank, reverse=True)
        self.list.blockSignals(True)
        self.list.clear()
        for event in events:
            tag = levels[event.event_id].level if levels else URGENCY_WORDS.get(event.urgency, event.urgency)
            item = QListWidgetItem(f"[{tag}] {event.title}" + (" (closed)" if event.closed_at is not None else ""))
            if levels:
                item.setToolTip(levels[event.event_id].text())
            item.setData(Qt.ItemDataRole.UserRole, event)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ignored = bool(event.statuses) and set(event.statuses) == {"ignored"}
            item.setCheckState(Qt.CheckState.Unchecked if ignored else Qt.CheckState.Checked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._count()

    def _tick(self, on: bool) -> None:
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def chosen(self) -> list:
        return [self.list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.list.count())
                if self.list.item(index).checkState() == Qt.CheckState.Checked]

    def _count(self, *_args) -> None:
        when = describe(self.start, self.now, self.format_time) if self.start else "the Events tab's period and filters"
        total, chosen = self.list.count(), len(self.chosen())
        self.summary.setText(f"<b>Period:</b> {when} · <b>{chosen}</b> of {total} events included")
        self.draft.setEnabled(True)

    def accept(self) -> None:
        self.store.set_setting("briefing.period", self.key())
        self.store.set_setting("briefing.custom_hours", str(self.hours.value()))
        self.events = self.chosen()
        super().accept()
