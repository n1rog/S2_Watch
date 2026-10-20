"""Configuration → Focus Regions → Custom Area: a radius around a place."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from s2_watch.focus import FocusArea
from s2_watch.locating import GENERAL_LEVELS, LocationResolver
from s2_watch.models import Message


class FocusAreaDialog(QDialog):
    def __init__(self, area: FocusArea | None, resolver: LocationResolver, parent=None) -> None:
        super().__init__(parent)
        self.resolver = resolver
        self.found = None
        self.setWindowTitle("Custom area of responsibility")
        intro = QLabel("Reports and events within this distance of a place are in focus, as well as any focus "
                       "regions you chose: they come first in lists, briefings and alerts.")
        intro.setWordWrap(True)
        self.place = QLineEdit(area.label if area else "")
        self.place.setPlaceholderText("Town, ST · County, ST · 36.06, -95.88 · EM26")
        self.place.textChanged.connect(self._typed)
        self.result = QLabel()
        self.radius = QSpinBox()
        self.radius.setRange(10, 1500)
        self.radius.setSingleStep(25)
        self.radius.setSuffix(" km")
        self.radius.setValue(area.km if area else 150)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow(intro)
        form.addRow("Centre:", self.place)
        form.addRow("", self.result)
        form.addRow("Radius:", self.radius)
        form.addRow(self.buttons)
        self._typed(self.place.text())

    def _typed(self, text: str) -> None:
        found = self.resolver.resolve(Message("Manual", text.strip()))[:1] if text.strip() else []
        self.found = found[0] if found and found[0].level not in GENERAL_LEVELS else None
        if not text.strip():
            self.result.setText("")
        elif self.found is None:
            self.result.setText("Not found as a point: try “Town, ST”, a county, coordinates or a grid square.")
        else:
            label = "coordinates" if self.found.label.startswith("Coordinates") else self.found.label
            self.result.setText(f"→ {label} ({self.found.lat:.3f}, {self.found.lon:.3f})")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.found is not None)

    def area(self) -> FocusArea | None:
        if self.found is None:
            return None
        label = self.place.text().strip() if self.found.label.startswith(("Coordinates", "Grid")) else self.found.label
        return FocusArea(label, self.found.lat, self.found.lon, self.radius.value())
