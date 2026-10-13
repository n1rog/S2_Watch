import re

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
)

from s2_watch.models import ANY_APPLICATION, APPLICATIONS, WatchForm

# How a form matches, in the operator's words; the stored form keeps text + use_regex.
EVERY, CONTAINS, WORDS, PATTERN = "every", "contains", "words", "pattern"
MATCH_CHOICES = (
    (EVERY, "Every message"),
    (CONTAINS, "Messages containing this text"),
    (WORDS, "Messages with any of these words"),
    (PATTERN, "Regular expression (advanced)"),
)
_WORD_LIST = re.compile(r"\\b\(\?:((?:[\w' -]|\\ |\\-)+(?:\|(?:[\w' -]|\\ |\\-)+)*)\)\\b")


def words_pattern(words: list[str]) -> str:
    """\\b(?:storm|power outage)\\b: whole words, any of them."""
    return r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b"


def pattern_words(pattern: str) -> list[str] | None:
    """The words of a pattern words_pattern made, or None for any other pattern."""
    match = _WORD_LIST.fullmatch(pattern)
    if not match:
        return None
    return [re.sub(r"\\(.)", r"\1", word) for word in match.group(1).split("|")]


class WatchFormDialog(QDialog):
    def __init__(
        self,
        parent=None,
        form: WatchForm | None = None,
        applications: tuple[str, ...] = (
            ANY_APPLICATION, *APPLICATIONS
        ),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit watch form" if form else "Add watch form")
        self.resize(620, 0)
        self.name = QLineEdit(form.name if form else "")
        self.name.setPlaceholderText("e.g. Bridge closures")
        self.application = QComboBox()
        self.application.addItems(applications)
        if form:
            index = self.application.findText(form.application)
            if index >= 0:
                self.application.setCurrentIndex(index)

        self.how = QComboBox()
        for key, label in MATCH_CHOICES:
            self.how.addItem(label, key)
        self.match_text = QPlainTextEdit()
        self.match_text.setTabChangesFocus(True)
        self.match_hint = QLabel()
        self.match_hint.setWordWrap(True)
        self.match_hint.setEnabled(False)       # greyed help text
        how, text = EVERY, ""
        if form and form.match_text:
            words = pattern_words(form.match_text) if form.use_regex else None
            if words is not None:
                how, text = WORDS, ", ".join(words)
            else:
                how, text = (PATTERN if form.use_regex else CONTAINS), form.match_text
        self.how.setCurrentIndex(self.how.findData(how))
        self.match_text.setPlainText(text)
        self.how.currentIndexChanged.connect(self._how_changed)

        self.message_type = QLineEdit(form.message_type if form else "")
        self.message_type.setPlaceholderText("Any type (e.g. STATREP, NEWS, EARTHQUAKE)")
        self.sender = QLineEdit(form.sender if form else "")
        self.sender.setPlaceholderText("Anyone (or a call sign, e.g. N1ROG)")
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")
        self.error.setWordWrap(True)

        layout = QFormLayout(self)
        layout.addRow("Name:", self.name)
        layout.addRow("Watch messages from:", self.application)
        layout.addRow("Keep:", self.how)
        layout.addRow("", self.match_text)
        layout.addRow("", self.match_hint)
        layout.addRow("Only this message type:", self.message_type)
        layout.addRow("Only from sender:", self.sender)
        layout.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self._how_changed()

    def _how_changed(self) -> None:
        how = self.how.currentData()
        self.match_text.setVisible(how != EVERY)
        self.match_text.setFixedHeight(140 if how == PATTERN else 60)
        self.match_hint.setText({
            EVERY: "Every message from the chosen application is kept and checked for news.",
            CONTAINS: "Kept when the message contains this text anywhere (capital letters don't matter).",
            WORDS: "Separate the words with commas, e.g.  bridge out, road closed, washout. "
                   "Whole words only, so “ice” doesn't match “police”.",
            PATTERN: "A Python regular expression, matched ignoring case.",
        }[how])
        self.error.clear()

    def _words(self) -> list[str]:
        return [word.strip() for word in self.match_text.toPlainText().replace("\n", ",").split(",") if word.strip()]

    def watch_form(self) -> WatchForm:
        how = self.how.currentData()
        text = self.match_text.toPlainText().strip()
        if how == EVERY:
            match_text, use_regex = "", False
        elif how == WORDS:
            words = self._words()
            match_text, use_regex = (words_pattern(words), True) if words else ("", False)
        else:
            match_text, use_regex = text, how == PATTERN
        return WatchForm(
            application=self.application.currentText(),
            name=self.name.text().strip(),
            match_text=match_text,
            use_regex=use_regex,
            message_type=self.message_type.text().strip(),
            sender=self.sender.text().strip(),
        )

    def accept(self) -> None:
        if not self.name.text().strip():
            self.error.setText("Give the form a name.")
            self.name.setFocus()
            return
        how = self.how.currentData()
        if how != EVERY and not self.match_text.toPlainText().strip():
            self.error.setText("Enter what to look for, or choose “Every message”.")
            self.match_text.setFocus()
            return
        if how == PATTERN:
            try:
                re.compile(self.match_text.toPlainText().strip())
            except re.error as problem:
                self.error.setText(f"That regular expression doesn't work: {problem}")
                self.match_text.setFocus()
                return
        super().accept()
