"""Reports → Add Intelligence Entry: a summary the operator is cleared to share (e.g. from
HSIN or a fusion center), and Reports → Intelligence Entries to review or withdraw them."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.locating import GENERAL_LEVELS, LocationResolver
from s2_watch.models import Message

HANDLING = ("TLP:CLEAR", "Approved for release to MAGNET", "Public information")


class AnalystEntryDialog(QDialog):
    def __init__(self, resolver: LocationResolver, last_source: str = "", parent=None) -> None:
        super().__init__(parent)
        self.resolver = resolver
        self.location = None
        self.setWindowTitle("Add intelligence entry")
        self.resize(640, 0)
        intro = QLabel("A summary you are cleared to share with the network, from a source S2 Watch can't read "
                       "itself (HSIN, a fusion center, an EOC briefing). It joins events, the map and briefings "
                       "like other open-source items. Don't paste restricted products: summarise what's releasable.")
        intro.setWordWrap(True)
        self.source = QLineEdit(last_source)
        self.source.setPlaceholderText("e.g. Oklahoma Information Fusion Center")
        self.official = QCheckBox("An official source (its entries confirm events, like NWS or USGS)")
        self.handling = QComboBox()
        self.handling.setEditable(True)
        self.handling.addItems(HANDLING)
        self.when = QDateTimeEdit(QDateTime.currentDateTime())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.category = QComboBox()
        self.category.addItems(NEWS_CATEGORIES)
        self.urgency = QComboBox()
        for label, value in (("High", "high"), ("Medium", "medium"), ("Low", "low")):
            self.urgency.addItem(label, value)
        self.urgency.setCurrentIndex(1)
        self.place = QLineEdit()
        self.place.setPlaceholderText("Town, ST · County, ST · coordinates · grid (optional)")
        self.place.textChanged.connect(self._place_typed)
        self.place_found = QLabel()
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("What happened, in releasable words")
        self.text.setFixedHeight(110)
        self.cleared = QCheckBox("I confirm this summary is cleared for release to the network")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        for signal in (self.source.textChanged, self.text.textChanged, self.cleared.toggled):
            signal.connect(self._check)
        form = QFormLayout()
        form.addRow("Source:", self.source)
        form.addRow("", self.official)
        form.addRow("Handling:", self.handling)
        form.addRow("When:", self.when)
        form.addRow("Category:", self.category)
        form.addRow("Urgency:", self.urgency)
        form.addRow("Where:", self.place)
        form.addRow("", self.place_found)
        form.addRow("Summary:", self.text)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.cleared)
        layout.addWidget(self.buttons)
        self._check()

    def _place_typed(self, text: str) -> None:
        found = self.resolver.resolve(Message("Manual", text.strip()))[:1] if text.strip() else []
        self.location = found[0] if found else None
        if not text.strip():
            self.place_found.setText("")
        elif self.location is None:
            self.place_found.setText("Not found; the entry will have no map position.")
        else:
            label = "coordinates" if self.location.label.startswith("Coordinates") else self.location.label
            vague = " (state or region only)" if self.location.level in GENERAL_LEVELS else ""
            self.place_found.setText(f"→ {label}{vague}")

    def _check(self) -> None:
        ready = bool(self.source.text().strip() and self.text.toPlainText().strip() and self.cleared.isChecked())
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(ready)

    def entry(self) -> dict:
        when = self.when.dateTime().toPython()
        when = when.astimezone(timezone.utc) if when.tzinfo else when.astimezone().astimezone(timezone.utc)
        return {"happened_at": when, "source": self.source.text().strip(), "handling": self.handling.currentText().strip(),
                "official": self.official.isChecked(), "category": self.category.currentText(),
                "urgency": self.urgency.currentData(), "place": self.place.text().strip(),
                "text": " ".join(self.text.toPlainText().split())}


class AnalystEntriesDialog(QDialog):
    remove_requested = Signal(int)
    add_requested = Signal()

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.setWindowTitle("Intelligence Entries")
        self.resize(900, 480)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("When", "Source", "Handling", "Category", "Where", "Summary"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.empty = QLabel("No entries yet. Use Add entry… for a summary you are cleared to share.")
        buttons = QDialogButtonBox()
        add = buttons.addButton("Add entry…", QDialogButtonBox.ButtonRole.ActionRole)
        add.clicked.connect(self.add_requested.emit)
        self.remove = buttons.addButton("Withdraw", QDialogButtonBox.ButtonRole.ActionRole)
        self.remove.setToolTip("Take it off the news lists and events (it stays in History)")
        self.remove.clicked.connect(self._remove)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.empty)
        layout.addWidget(self.table)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        entries = self.store.analyst_entries()
        self.table.setRowCount(0)
        for entry in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (self.format_time(entry["happened_at"]), entry["source"] + (" (official)" if entry["official"] else ""),
                      entry["handling"], entry["category"], entry["place"], entry["text"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                if column == 5:
                    item.setToolTip(entry["text"])
                self.table.setItem(row, column, item)
        self.empty.setVisible(not entries)
        self._selection_changed()

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None

    def _selection_changed(self) -> None:
        self.remove.setEnabled(self._selected() is not None)

    def _remove(self) -> None:
        entry_id = self._selected()
        if entry_id is not None:
            self.remove_requested.emit(entry_id)
