"""Fix location: put a report where it really is, and remember it for future messages."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from s2_watch.analysis.ollama import readable_text
from s2_watch.geocoding import GeocodingError
from s2_watch.locating import GENERAL_LEVELS, LocationResolver, ResolvedLocation, normalize
from s2_watch.models import Message

LEVEL_WORDS = {"precise": "exact point", "city": "town", "county": "county", "grid": "grid square",
               "state": "state only", "region": "MARS region only"}


def describe(location: ResolvedLocation | None) -> str:
    if location is None:
        return "no location"
    label = "coordinates" if location.label.startswith("Coordinates") else location.label
    region = f" · MARS Region {location.region}" if location.region else ""
    return f"{label} ({LEVEL_WORDS.get(location.level, location.level)}){region}"


def guess_phrase(message: Message, typed: str, current: ResolvedLocation | None) -> str:
    """The words in the message that name the place: the typed town's name if the message
    uses it, else the name the message was wrongly placed by."""
    text = normalize(readable_text(message))
    for candidate in (typed.split(",")[0], current.label.split(",")[0] if current else ""):
        name = normalize(candidate)
        if name and not re.fullmatch(r"[\d\s.,+-]+", name) and re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return ""


class FixLocationDialog(QDialog):
    def __init__(self, message: Message, current: ResolvedLocation | None, resolver: LocationResolver,
                 geocode=None, parent=None) -> None:
        """``geocode(query) -> (lat, lon) | None`` looks a place up on OpenStreetMap (asked for
        by the operator; S2 Watch's own place list comes first)."""
        super().__init__(parent)
        self.message = message
        self.current = current
        self.resolver = resolver
        self.geocode = geocode
        self.location: ResolvedLocation | None = None
        self.setWindowTitle("Fix location")
        self.resize(640, 0)
        text = " ".join(readable_text(message).split())
        shown = QLabel(f"<i>{text[:300]}{'…' if len(text) > 300 else ''}</i>")
        shown.setWordWrap(True)
        self.place = QLineEdit()
        self.place.setPlaceholderText("Town, ST · County, ST · 36.06, -95.88 · EM26ab")
        self.place.textChanged.connect(self._typed)
        self.found = QLabel()
        self.found.setWordWrap(True)
        self.lookup = QPushButton("Look up on OpenStreetMap")
        self.lookup.setToolTip("Sends what you typed to OpenStreetMap (Nominatim) to find a town S2 Watch "
                               "doesn't know")
        self.lookup.setVisible(False)
        self.lookup.clicked.connect(self._look_up)
        found_row = QHBoxLayout()
        found_row.addWidget(self.found, 1)
        found_row.addWidget(self.lookup)
        self.remember = QCheckBox("Remember: wherever a message says")
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText("the words that name the place, e.g. BIXBY")
        self.remember.toggled.connect(self.phrase.setEnabled)
        remember_row = QHBoxLayout()
        remember_row.addWidget(self.remember)
        remember_row.addWidget(self.phrase, 1)
        note = QLabel("Unchecked, the correction applies to this message only.")
        note.setEnabled(False)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Placed at:", QLabel(describe(current)))
        form.addRow("Where it really is:", self.place)
        form.addRow("", found_row)
        layout = QVBoxLayout(self)
        layout.addWidget(shown)
        layout.addLayout(form)
        layout.addLayout(remember_row)
        layout.addWidget(note)
        layout.addWidget(self.buttons)
        self._typed("")

    def _typed(self, text: str) -> None:
        text = text.strip()
        found = self.resolver.resolve(Message("Manual", text))[:1] if text else []
        self.location = found[0] if found else None
        town_asked = bool(re.search(r"[A-Za-z]{2,}.*,\s*[A-Za-z]{2}\b", text))
        vague = self.location is None or self.location.level in GENERAL_LEVELS
        self.lookup.setVisible(bool(text) and town_asked and vague and self.geocode is not None)
        if not text:
            self.found.setText("")
        elif self.location is None:
            self.found.setText("Not found. Try “Town, ST”, a county, coordinates or a grid square.")
        else:
            self.found.setText(f"→ {describe(self.location)}"
                               + (" — that's only the state; look the town up?" if vague and town_asked else ""))
        self._update_phrase(text)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.location is not None)

    def _update_phrase(self, text: str) -> None:
        guess = guess_phrase(self.message, text, self.current)
        if not self.phrase.isModified():
            self.phrase.setText(guess)
        self.remember.setChecked(bool(self.phrase.text().strip()))
        self.phrase.setEnabled(self.remember.isChecked())

    def _look_up(self) -> None:
        text = self.place.text().strip()
        try:
            coordinates = self.geocode(f"{text}, USA") if self.geocode else None
        except GeocodingError as error:
            self.found.setText(f"OpenStreetMap couldn't be reached: {error}")
            return
        if not coordinates:
            self.found.setText(f"OpenStreetMap doesn't know “{text}” either.")
            return
        lat, lon = coordinates
        state, region = self.resolver.gazetteer.locate_point(lat, lon)
        name, _, code = text.rpartition(",")
        self.location = ResolvedLocation("city", lat, lon, f"{name.strip().title()}, {code.strip().upper()}",
                                         state, region)
        self.found.setText(f"→ {describe(self.location)} (OpenStreetMap)")
        self.lookup.setVisible(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def correction_key(self) -> str | None:
        """The phrase to remember, or None for this message only."""
        phrase = self.phrase.text().strip()
        return phrase if self.remember.isChecked() and phrase else None
