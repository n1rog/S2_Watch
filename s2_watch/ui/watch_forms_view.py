"""Configuration → Watch Forms: which messages S2 Watch keeps, and which OSINT sources it fetches.

Forms are grouped (topics, whole applications, the operator's own) and described in plain
words; the full pattern is in each form's tooltip. Each OSINT source has its own on/off box.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS
from s2_watch.models import ANY_APPLICATION, WatchForm

FORM_ROLE = Qt.ItemDataRole.UserRole
GROUPS = (
    ("topics", "Topics", "keywords for each kind of news, checked in messages from every application"),
    ("applications", "Whole applications", "every message from one application, whatever it says"),
    ("custom", "Your forms", "forms you added or imported"),
)


def _split_alternatives(pattern: str) -> list[str]:
    """Split a regular expression at its top-level | signs."""
    parts, depth, start, index, in_class = [], 0, 0, 0, False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(pattern[start:index])
            start = index + 1
        index += 1
    parts.append(pattern[start:])
    return parts


def _closing_paren(pattern: str, opening: int) -> int:
    depth, index, in_class = 0, opening, False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _readable_word(alternative: str) -> str:
    """The word an alternative spells: literal letters, plus the first choice of each group
    that must match ("stabb(?:ed|ing)" -> "stabbed"); optional endings are left off."""
    text = re.sub(r"\[-\\s\]\?|\\s[*+?]?|\\-", " ", alternative)
    word, index = "", 0
    while index < len(text):
        literal = re.match(r"[A-Za-z0-9 '&-]+", text[index:])
        if literal:
            piece = literal.group()
            index += len(piece)
            if index < len(text) and text[index] in "?*":       # "storms?" -> "storms" is fine,
                piece = piece[:-1] if len(piece) > 1 else piece  # but "s?" alone adds nothing
            word += piece
            continue
        if text.startswith("(?:", index):
            closing = _closing_paren(text, index)
            if closing < 0:
                break
            optional = closing + 1 < len(text) and text[closing + 1] in "?*"
            if not optional and word.strip():
                choice = _split_alternatives(text[index + 3:closing])[0]
                word += _readable_word(choice)
            index = closing + 1 + (1 if optional else 0)
            continue
        break   # a character class or other syntax: stop at what is readable
    word = " ".join(word.split())
    if not word or not word[0].isalnum():
        return ""
    return re.sub(r"\s+\S$", "", word)    # "gun m[ae]n" -> "gun"


def pattern_keywords(pattern: str) -> list[str]:
    """The readable words a keyword pattern looks for, in order (e.g. riot, protest, unrest)."""
    words: list[str] = []

    def walk(part: str) -> None:
        for alternative in _split_alternatives(part):
            alternative = alternative.strip()
            while alternative.startswith("\\b"):
                alternative = alternative[2:]
            while alternative.endswith("\\b"):
                alternative = alternative[:-2]
            if alternative.startswith("(?:") and _closing_paren(alternative, 0) == len(alternative) - 1:
                walk(alternative[3:-1])
                continue
            word = _readable_word(alternative)
            if word and word.lower() not in (known.lower() for known in words):
                words.append(word)

    walk(pattern)
    return words


def describe_form(form: WatchForm, shown: int = 6) -> str:
    """What a form matches, in plain words."""
    if not form.match_text:
        description = "Every message"
    elif form.use_regex:
        words = pattern_keywords(form.match_text)
        if words:
            more = len(words) - shown
            description = "Keywords: " + ", ".join(words[:shown]) + (f"… (+{more} more)" if more > 0 else "")
        else:
            description = "Pattern (see tooltip)"
    else:
        description = f"Text contains “{form.match_text}”"
    if form.message_type:
        description += f" · type {form.message_type}"
    if form.sender:
        description += f" · from {form.sender}"
    return description


def form_group(form: WatchForm) -> str:
    if form.application == ANY_APPLICATION and form.name in CATEGORY_PATTERNS:
        return "topics"
    if form.application != ANY_APPLICATION and not (form.match_text or form.message_type or form.sender):
        return "applications"
    return "custom"


class WatchFormsPage(QWidget):
    toggled = Signal(object, bool)          # WatchForm, enabled
    add_requested = Signal()
    edit_requested = Signal(object)         # WatchForm
    remove_requested = Signal(object)       # WatchForm
    import_requested = Signal()
    export_requested = Signal()
    osint_source_toggled = Signal(str, bool)

    def __init__(self, osint_sources: dict[str, tuple[str, str]], enabled_sources: set[str], parent=None) -> None:
        """``osint_sources``: key -> (label, how often it is fetched)."""
        super().__init__(parent)
        intro = QLabel("S2 Watch keeps a message when a checked form matches it, then decides whether it is news. "
                       "Uncheck a form to pause it; double-click to edit it.")
        intro.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(("Form", "Applies to", "Matches"))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(lambda item, _column: self._edit(item))
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)

        self.add_button = QPushButton("Add form…")
        self.add_button.clicked.connect(self.add_requested.emit)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(lambda: self._edit(self.tree.currentItem()))
        self.remove_button = QPushButton("Remove…")
        self.remove_button.clicked.connect(self._remove)
        import_button = QPushButton("Import…")
        import_button.setToolTip("Add watch forms from a file someone exported")
        import_button.clicked.connect(self.import_requested.emit)
        export_button = QPushButton("Export…")
        export_button.setToolTip("Save all watch forms to a file to share or keep")
        export_button.clicked.connect(self.export_requested.emit)
        buttons = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(import_button)
        buttons.addWidget(export_button)

        forms_box = QGroupBox("Watch forms")
        forms_layout = QVBoxLayout(forms_box)
        forms_layout.addWidget(intro)
        forms_layout.addWidget(self.tree)
        forms_layout.addLayout(buttons)

        osint_box = QGroupBox("OSINT sources (official and open feeds from the internet)")
        osint_layout = QVBoxLayout(osint_box)
        note = QLabel("Turning a source off stops new items from it. Reports already saved stay until they "
                      "leave the watch period.")
        note.setWordWrap(True)
        osint_layout.addWidget(note)
        # One row per source: its switch, how often it's fetched, and how it's doing.
        self.osint_table = QTableWidget(len(osint_sources), 3)
        self.osint_table.setHorizontalHeaderLabels(("Source", "Fetched", "Status"))
        self.osint_table.verticalHeader().setVisible(False)
        self.osint_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.osint_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.osint_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.osint_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.osint_boxes: dict[str, QCheckBox] = {}
        self.osint_status: dict[str, QTableWidgetItem] = {}
        for row, (key, (label, schedule)) in enumerate(osint_sources.items()):
            box = QCheckBox(label)
            box.setChecked(key in enabled_sources)
            box.toggled.connect(lambda on, key=key: self.osint_source_toggled.emit(key, on))
            box.toggled.connect(lambda _on: self.set_osint_status(self._last_status))
            self.osint_table.setCellWidget(row, 0, box)
            self.osint_table.setItem(row, 1, QTableWidgetItem(schedule))
            status = QTableWidgetItem("")
            self.osint_table.setItem(row, 2, status)
            self.osint_boxes[key] = box
            self.osint_status[key] = status
        self.osint_table.resizeRowsToContents()
        self._last_status: dict[str, str] = {}
        osint_layout.addWidget(self.osint_table)

        # The divider between forms and sources can be dragged.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(forms_box)
        splitter.addWidget(osint_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.set_osint_status({})
        self._selection_changed()

    def set_forms(self, forms: list[WatchForm]) -> None:
        selected = self.selected_form()
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {}
        for key, title, explanation in GROUPS:
            members = sorted((form for form in forms if form_group(form) == key),
                             key=lambda form: (form.application, form.name.lower()))
            if not members:
                continue
            heading = QTreeWidgetItem([f"{title} — {explanation}"])
            font = QFont(heading.font(0))
            font.setBold(True)
            heading.setFont(0, font)
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(heading)
            heading.setFirstColumnSpanned(True)
            groups[key] = heading
            for form in members:
                item = QTreeWidgetItem([form.name, form.application, describe_form(form)])
                item.setData(0, FORM_ROLE, form)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if form.enabled else Qt.CheckState.Unchecked)
                tooltip = form.match_text or "Every message"
                item.setToolTip(2, tooltip if len(tooltip) < 1500 else tooltip[:1500] + "…")
                heading.addChild(item)
                if selected and (form.application, form.name) == (selected.application, selected.name):
                    self.tree.setCurrentItem(item)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._selection_changed()

    def set_osint_status(self, status: dict[str, str]) -> None:
        """Per source: "updated 14:02", "failing: ...", or "" when not fetched yet."""
        self._last_status = dict(status)
        for key, item in self.osint_status.items():
            item.setText(status.get(key, "") if self.osint_boxes[key].isChecked() else "off")
            item.setToolTip(item.text())

    def selected_form(self) -> WatchForm | None:
        item = self.tree.currentItem()
        form = item.data(0, FORM_ROLE) if item is not None else None
        return form if isinstance(form, WatchForm) else None

    def _selection_changed(self) -> None:
        chosen = self.selected_form() is not None
        self.edit_button.setEnabled(chosen)
        self.remove_button.setEnabled(chosen)

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        form = item.data(0, FORM_ROLE)
        if column == 0 and isinstance(form, WatchForm):
            enabled = item.checkState(0) == Qt.CheckState.Checked
            if enabled != form.enabled:
                self.toggled.emit(form, enabled)

    def _edit(self, item) -> None:
        form = item.data(0, FORM_ROLE) if item is not None else None
        if isinstance(form, WatchForm):
            self.edit_requested.emit(form)

    def _remove(self) -> None:
        form = self.selected_form()
        if form is not None:
            self.remove_requested.emit(form)

    def _menu(self, position) -> None:
        item = self.tree.itemAt(position)
        form = item.data(0, FORM_ROLE) if item is not None else None
        if not isinstance(form, WatchForm):
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Edit…").triggered.connect(lambda: self.edit_requested.emit(form))
        menu.addAction("Remove…").triggered.connect(lambda: self.remove_requested.emit(form))
        menu.exec(self.tree.viewport().mapToGlobal(position))
