"""Review, edit and accept an event's assessment (Ollama's proposal, the accepted one, or a new
one written from scratch), ask Ollama about it, and look back at its history."""

from __future__ import annotations

import re
import threading
from dataclasses import replace
from datetime import datetime, timezone
from html import escape

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from s2_watch.event_assessment import LEVELS, TREND_MARKS, TRENDS, Discussion, EventAssessment, assessment_text


def _lines(editor: QPlainTextEdit) -> list[str]:
    seen, lines = set(), []
    for line in editor.toPlainText().splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line and line.lower() not in seen:
            seen.add(line.lower())
            lines.append(line)
    return lines


def _paragraphs(text: str) -> list[str]:
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]


# Where an answer can be used: field, label, and whether it replaces the field or is added to it.
ANSWER_USES = (("bluf", "Replace the BLUF", "replace"), ("trend_reason", "Replace the trend's Why", "replace"),
               ("agreement", "Replace Agreement", "replace"), ("assessment", "Add to Assessment", "paragraph"),
               ("gaps", "Add to Intelligence gaps", "line"), ("mitigation", "Add to Mitigation", "line"),
               ("guidance", "Add to MAGNET guidance", "line"))


class AssessmentDialog(QDialog):
    """The assessment's sections as editable fields; Accept assessment makes it the event's assessment.
    With a Discussion, a panel beside them asks Ollama about the assessment as it stands in the fields;
    each answer can be used in a field or rejected (and then forgotten by the model)."""

    answered = Signal(str, str)         # answer, error (from a thread)

    def __init__(self, title: str, assessment: EventAssessment, report_count: int, analyst: str, parent=None,
                 heading: str = "", discussion: Discussion | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Assessment — {title}")
        self.resize(1320 if discussion else 820, 760)
        self.source = assessment
        self.report_count = report_count
        self.analyst = analyst
        self.accepted: EventAssessment | None = None
        self.discussion = discussion
        self.asking = False
        intro = QLabel(heading or "Check each statement against the numbered reports in the event's details, "
                                  "correct it, then Accept assessment. Numbers in brackets cite those reports.")
        intro.setWordWrap(True)
        self.bluf = self._text(assessment.bluf, 70)
        self.trend = QComboBox()
        for trend in TRENDS:
            self.trend.addItem(f"{TREND_MARKS[trend]} {trend}", trend)
        self.trend.setCurrentIndex(max(0, self.trend.findData(assessment.trend)))
        self.trend_reason = QLineEdit(assessment.trend_reason)
        self.trend_reason.setPlaceholderText("why, with the reports it rests on, e.g. intensity fell [18, 22]")
        self.agreement = self._text(assessment.agreement, 50)
        self.assessment = self._text("\n\n".join(assessment.assessment), 110)
        self.assessment.setPlaceholderText("Paragraphs in measured language (assessed, likely, indicates, suggests); "
                                           "a blank line between paragraphs")
        self.probabilities = self._text("\n".join(f"{s} | {level}" for s, level in assessment.probabilities), 70)
        self.probabilities.setPlaceholderText("One per line: what may happen next | HIGH, MODERATE or LOW")
        self.gaps = self._text("\n".join(f"- {gap}" for gap in assessment.gaps), 70)
        self.mitigation = self._text("\n".join(f"- {item}" for item in assessment.mitigation), 60)
        self.guidance = self._text("\n".join(f"- {item}" for item in assessment.guidance), 60)
        for editor in (self.gaps, self.mitigation, self.guidance):
            editor.setPlaceholderText("One per line")
        form = QFormLayout()
        form.addRow("BLUF", self.bluf)
        form.addRow("Trend", self.trend)
        form.addRow("Why", self.trend_reason)
        form.addRow("Agreement", self.agreement)
        form.addRow("Assessment", self.assessment)
        form.addRow("Probability", self.probabilities)
        form.addRow("Intelligence gaps", self.gaps)
        form.addRow("Mitigation", self.mitigation)
        form.addRow("MAGNET guidance", self.guidance)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        accept = buttons.addButton("Accept assessment", QDialogButtonBox.ButtonRole.AcceptRole)
        accept.setToolTip(f"Make the fields the event's assessment, accepted by {analyst or 'you'}, and close")
        for button in buttons.buttons():     # Enter never accepts or cancels: only a click does
            button.setAutoDefault(False)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        if discussion is None:
            layout.addLayout(form)
        else:
            fields = QWidget()
            fields.setLayout(form)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(fields)
            split = QSplitter(Qt.Orientation.Horizontal)
            split.addWidget(scroll)
            split.addWidget(self._discussion_panel())
            split.setSizes([800, 520])
            layout.addWidget(split, 1)
        layout.addWidget(buttons)

    def _discussion_panel(self) -> QWidget:
        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.anchorClicked.connect(self._answer_link)
        self.turns: list[dict] = []      # {"who", "text", "state"}: state "", "rejected" or what it was used for
        self.transcript.setHtml("<p style='color:gray'>Ask Ollama about this assessment: why it says something, "
                                "which report a statement rests on, what the reports leave out. It answers from "
                                "the event's reports and the assessment as it stands in the fields, citing "
                                "reports by number. Its answers don't change the assessment unless you use "
                                "one in a field.</p>")
        self.question = QLineEdit()
        self.question.setPlaceholderText("e.g. Why is the trend escalating?")
        self.ask_button = QPushButton("Ask")
        self.ask_button.setAutoDefault(False)
        self.ask_button.clicked.connect(self.ask)
        self.ask_status = QLabel("")
        self.ask_status.setWordWrap(True)
        self.answered.connect(self._answered)
        row = QHBoxLayout()
        row.addWidget(self.question, 1)
        row.addWidget(self.ask_button)
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(QLabel(f"<b>Ask about this assessment</b> ({escape(self.discussion.model)})"))
        column.addWidget(self.transcript, 1)
        column.addWidget(self.ask_status)
        column.addLayout(row)
        return panel

    def keyPressEvent(self, event) -> None:
        # Enter in the question asks it; elsewhere it does nothing (it once accepted the assessment).
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.discussion is not None and self.focusWidget() is self.question:
                self.ask()
            return
        super().keyPressEvent(event)

    def current_text(self) -> str:
        """The assessment as it stands in the fields (unchecked), for the model to read."""
        probabilities = [(statement.strip() or level.strip(), level.strip().upper() if statement.strip() else "")
                         for statement, _, level in (line.rpartition("|") for line in _lines(self.probabilities))]
        return assessment_text(EventAssessment(
            bluf=" ".join(self.bluf.toPlainText().split()), trend=self.trend.currentData(),
            trend_reason=self.trend_reason.text().strip(), agreement=" ".join(self.agreement.toPlainText().split()),
            assessment=_paragraphs(self.assessment.toPlainText()), probabilities=probabilities,
            gaps=_lines(self.gaps), mitigation=_lines(self.mitigation), guidance=_lines(self.guidance)))

    def ask(self) -> None:
        question = " ".join(self.question.text().split())
        if self.discussion is None or self.asking or not question:
            return
        self.asking = True
        self.question.setFocus()         # not passed on to Accept when the Ask button is disabled
        self.ask_button.setEnabled(False)
        self.ask_status.setText("Ollama is thinking… a minute or two on a computer without a suitable graphics card.")
        self._add_turn("You", question)
        self.question.clear()
        discussion, assessment = self.discussion, self.current_text()

        def work() -> None:
            try:
                self.answered.emit(discussion.ask(question, assessment), "")
            except Exception as error:      # noqa: BLE001 - shown to the operator
                self.answered.emit("", str(error) or type(error).__name__)

        threading.Thread(target=work, daemon=True, name="s2-watch-assessment-question").start()

    def _answered(self, answer: str, error: str) -> None:
        self.asking = False
        self.ask_button.setEnabled(True)
        self.ask_status.setText(f"<span style='color:#c62828'>No answer: {escape(error)}</span>" if error else "")
        if answer:
            self._add_turn("Ollama", answer)
        self.question.setFocus()

    def _add_turn(self, who: str, text: str) -> None:
        self.turns.append({"who": who, "text": text, "state": ""})
        self._show_turns()

    def _show_turns(self) -> None:
        out = []
        for number, turn in enumerate(self.turns):
            text = escape(turn["text"])
            if turn["who"] == "You":
                out.append(f"<p><b>You:</b> {text}</p>")
            elif turn["state"] == "rejected":
                out.append(f"<p style='color:gray'><b>Ollama:</b> <s>{text}</s><br><i>Rejected: left out of the "
                           f"conversation from now on.</i></p>")
            else:
                actions = (f"<i>✓ {escape(turn['state'])}.</i> " if turn["state"] else "") + \
                    f"<a href='use:{number}'>Use this answer ▾</a> · <a href='reject:{number}'>Reject</a>"
                out.append(f"<p><b>Ollama:</b> {text}<br><small>{actions}</small></p>")
        self.transcript.setHtml("".join(out))
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _answer_link(self, url: QUrl) -> None:
        action, _, number = url.toString().partition(":")
        if not number.isdigit() or int(number) >= len(self.turns):
            return
        if action == "reject":
            self.reject_answer(int(number))
        elif action == "use":
            menu = QMenu(self)
            for name, label, _how in ANSWER_USES:
                menu.addAction(label).triggered.connect(
                    lambda _checked=False, name=name: self.use_answer(int(number), name))
            menu.exec(QCursor.pos())

    def use_answer(self, number: int, name: str) -> None:
        """Put answer ``number`` into a field (see ANSWER_USES); the analyst edits it from there."""
        label, how = next((label, how) for field, label, how in ANSWER_USES if field == name)
        text, editor = self.turns[number]["text"], getattr(self, name)
        if how == "replace":
            editor.setText(text) if isinstance(editor, QLineEdit) else editor.setPlainText(text)
        else:
            current = editor.toPlainText().rstrip()
            joiner = "\n\n" if how == "paragraph" else "\n"
            addition = text if how == "paragraph" else f"- {text}"
            editor.setPlainText(f"{current}{joiner}{addition}" if current else addition)
        self.turns[number]["state"] = label.replace("Replace", "Replaced").replace("Add to", "Added to")
        self._show_turns()

    def reject_answer(self, number: int) -> None:
        """Mark an answer rejected; the model isn't shown it again."""
        self.turns[number]["state"] = "rejected"
        self.discussion.forget(self.turns[number]["text"])
        self._show_turns()

    def done(self, result: int) -> None:
        # The model stays loaded during the conversation; unloaded once the window closes.
        if self.discussion is not None:
            threading.Thread(target=self.discussion.release, daemon=True, name="s2-watch-release-model").start()
        super().done(result)

    @staticmethod
    def _text(text: str, height: int) -> QPlainTextEdit:
        editor = QPlainTextEdit(text)
        editor.setMinimumHeight(height)
        return editor

    def result_assessment(self) -> EventAssessment | None:
        """The edited assessment, or None (with a message) when a probability line has no level."""
        probabilities = []
        for line in _lines(self.probabilities):
            statement, _, level = line.rpartition("|")
            level = level.strip().upper()
            if not statement.strip() or level not in LEVELS:
                QMessageBox.warning(self, "Assessment", f"Probability lines are written “what may happen | HIGH, "
                                                        f"MODERATE or LOW”:\n\n{line}")
                return None
            probabilities.append((" ".join(statement.split()), level))
        bluf = " ".join(self.bluf.toPlainText().split())
        paragraphs = _paragraphs(self.assessment.toPlainText())
        if not bluf and not paragraphs:
            QMessageBox.warning(self, "Assessment", "Write at least a BLUF or an assessment paragraph.")
            return None
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return replace(
            self.source, bluf=bluf, trend=self.trend.currentData(), trend_reason=self.trend_reason.text().strip(),
            agreement=" ".join(self.agreement.toPlainText().split()), assessment=paragraphs,
            probabilities=probabilities, gaps=_lines(self.gaps), mitigation=_lines(self.mitigation),
            guidance=_lines(self.guidance), accepted_by=self.analyst, accepted_at=now,
            reports=self.source.reports or self.report_count, created_at=self.source.created_at or now)

    def _accept(self) -> None:
        self.accepted = self.result_assessment()
        if self.accepted is not None:
            self.accept()


class AssessmentHistoryDialog(QDialog):
    """Every accepted version of an event's assessment, newest first."""

    def __init__(self, title: str, history: list[EventAssessment], format_time, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Assessment history — {title}")
        self.resize(760, 620)
        browser = QTextBrowser()
        out = []
        for number, version in enumerate(history):
            when = format_time(datetime.fromisoformat(version.accepted_at)) if version.accepted_at else ""
            label = "current" if number == 0 else "replaced"
            out.append(f"<h3 style='margin-bottom:0'>{escape(when)} — {TREND_MARKS.get(version.trend, '')} "
                       f"{escape(version.trend)} <span style='color:gray'>({label})</span></h3>"
                       f"<p style='color:gray;margin-top:0'>{escape(version.credit)}, from {version.reports} reports</p>")
            if version.bluf:
                out.append(f"<p><b>BLUF:</b> {escape(version.bluf)}</p>")
            out += [f"<p>{escape(p)}</p>" for p in version.assessment]
            if version.probabilities:
                out.append("<p>" + "<br>".join(f"{escape(s)} — <b>{level}</b>" for s, level in version.probabilities)
                           + "</p>")
            out.append("<hr>")
        browser.setHtml("".join(out) or "<p>No accepted assessment yet.</p>")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(browser)
        layout.addWidget(buttons)
