"""Review, edit and accept an event's assessment (Ollama's proposal, the accepted one, or a new
one written from scratch), and look back at its history."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from html import escape

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QTextBrowser,
    QVBoxLayout,
)

from s2_watch.event_assessment import LEVELS, TREND_MARKS, TRENDS, EventAssessment


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


class AssessmentDialog(QDialog):
    """The assessment's sections as editable fields; Accept makes it the event's assessment."""

    def __init__(self, title: str, assessment: EventAssessment, report_count: int, analyst: str, parent=None,
                 heading: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Assessment — {title}")
        self.resize(820, 760)
        self.source = assessment
        self.report_count = report_count
        self.analyst = analyst
        self.accepted: EventAssessment | None = None
        intro = QLabel(heading or "Check each statement against the numbered reports in the event's details, "
                                  "correct it, then Accept. Numbers in brackets cite those reports.")
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
        accept = buttons.addButton("Accept", QDialogButtonBox.ButtonRole.AcceptRole)
        accept.setToolTip(f"Make this the event's assessment, accepted by {analyst or 'you'}")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)

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
