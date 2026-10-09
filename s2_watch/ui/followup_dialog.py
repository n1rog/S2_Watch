"""Draft a follow-up or a briefing: edit the full report, then copy or save its FLMSG and
JS8Call versions (made from the full report as it is edited)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from s2_watch.analysis.ollama import draft_assessment
from s2_watch.followup import JS8CALL_TARGET, FollowUpFacts, dtg, flmsg_text, full_report, js8call_text


def replace_section(report: str, subject: str, assessment: str) -> str:
    """The report with a new SUBJECT line and everything after "ASSESSMENT:" replaced."""
    lines = report.splitlines()
    if subject:
        lines = [f"SUBJECT: {subject}" if line.startswith("SUBJECT:") else line for line in lines]
    if "ASSESSMENT:" in lines:
        lines = lines[:lines.index("ASSESSMENT:") + 1] + [assessment]
    else:
        lines += ["", "ASSESSMENT:", assessment]
    return "\n".join(lines)


class FollowUpDialog(QDialog):
    suggestion_ready = Signal(str, str, str)   # subject, assessment, error (from a thread)

    def __init__(self, facts: FollowUpFacts | None, store, parent=None, ollama: tuple[str, str] | None = None, *,
                 text: str | None = None, js8=js8call_text, title: str = "Draft follow-up report",
                 file_prefix: str = "S2-FOLLOWUP", suggest_fn=draft_assessment,
                 suggest_label: str = "Suggest subject && assessment (Ollama)",
                 mark_label: str = "Mark report “Follow-up created”", marked_label: str = "Marked “Follow-up created”",
                 callsign: str = "") -> None:
        super().__init__(parent)
        self.ollama = ollama   # (endpoint, model) when Ollama is the analysis mode
        self.setWindowTitle(title)
        self.resize(760, 640)
        self.facts = facts
        self.store = store
        self.marked = False
        self.js8 = js8
        self.file_prefix = file_prefix
        self.suggest_fn = suggest_fn
        self.suggest_label = suggest_label
        self.marked_label = marked_label
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.callsign = QLineEdit(facts.callsign if facts else callsign)
        self.callsign.setPlaceholderText("e.g. N1ROG")
        self.callsign.editingFinished.connect(self._callsign_changed)
        top = QFormLayout()
        top.addRow("Your callsign", self.callsign)

        self.full = QPlainTextEdit(text if text is not None else full_report(facts))
        self.full.setFont(fixed)
        self.full.textChanged.connect(self._derive)
        self.flmsg = QPlainTextEdit(readOnly=True)
        self.flmsg.setFont(fixed)
        self.js8call = QPlainTextEdit(readOnly=True)
        self.js8call.setFont(fixed)
        self.js8call_count = QLabel()

        self.tabs = QTabWidget()
        self.tabs.addTab(self._page(
            "Write the full report first; the FLMSG and JS8Call versions are made from it as you edit.",
            self.full), "1. Full report")
        save = QPushButton("Save as text file…")
        save.clicked.connect(self._save_flmsg)
        self.tabs.addTab(self._page(
            "Plain upper-case text wrapped for radio — paste into an FLMSG form (e.g. ICS-213 or blank).",
            self.flmsg, save), "2. FLMSG")
        self.tabs.addTab(self._page(
            f"A short message from the full report's fields (aim for {JS8CALL_TARGET} characters or fewer).",
            self.js8call, self.js8call_count), "3. JS8Call")

        buttons = QDialogButtonBox()
        copy = buttons.addButton("Copy this tab", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self._copy)
        self.suggest = buttons.addButton(suggest_label, QDialogButtonBox.ButtonRole.ActionRole)
        self.suggest.setToolTip("Your local Ollama model drafts the subject and assessment from this report; "
                                "check and edit them before you send anything.")
        self.suggest.setVisible(ollama is not None)
        self.suggest.clicked.connect(self._suggest)
        self.suggestion_ready.connect(self._suggested)
        self.mark = buttons.addButton(mark_label, QDialogButtonBox.ButtonRole.ActionRole)
        self.mark.clicked.connect(self._mark)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)
        self._derive()

    @staticmethod
    def _page(text: str, editor: QPlainTextEdit, extra: QWidget | None = None) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(text)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(editor)
        if extra is not None:
            row = QHBoxLayout()
            row.addWidget(extra)
            row.addStretch(1)
            layout.addLayout(row)
        return page

    def _callsign_changed(self) -> None:
        callsign = self.callsign.text().strip().upper()
        self.callsign.setText(callsign)
        self.store.set_setting("operator.callsign", callsign)
        text = self.full.toPlainText()
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line.startswith("FROM:"):
                lines[index] = f"FROM: {callsign or '(your callsign)'}"
                break
        if "\n".join(lines) != text:
            self.full.setPlainText("\n".join(lines))

    def _derive(self) -> None:
        report = self.full.toPlainText()
        self.flmsg.setPlainText(flmsg_text(report))
        message = self.js8(report)
        self.js8call.setPlainText(message)
        over = len(message) > JS8CALL_TARGET
        self.js8call_count.setText(f"{len(message)} characters" + (" — consider shortening the subject" if over else ""))

    def _copy(self) -> None:
        editor = (self.full, self.flmsg, self.js8call)[self.tabs.currentIndex()]
        QGuiApplication.clipboard().setText(editor.toPlainText())

    def _save_flmsg(self) -> None:
        when = (self.facts.now or self.facts.message.received_at) if self.facts else datetime.now(timezone.utc)
        name = f"{self.file_prefix}-" + dtg(when).replace(" ", "-") + ".txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save FLMSG text", str(Path.home() / name), "Text files (*.txt)")
        if path:
            Path(path).write_text(self.flmsg.toPlainText(), encoding="ascii", errors="replace")

    def _suggest(self) -> None:
        if not self.ollama:
            return
        endpoint, model = self.ollama
        report = self.full.toPlainText()
        self.suggest.setEnabled(False)
        self.suggest.setText("Ollama is writing…")

        def work() -> None:
            try:
                subject, assessment = self.suggest_fn(report, endpoint, model)
                self.suggestion_ready.emit(subject, assessment, "")
            except Exception as error:
                self.suggestion_ready.emit("", "", str(error))

        threading.Thread(target=work, daemon=True, name="s2-watch-assessment").start()

    def _suggested(self, subject: str, assessment: str, error: str) -> None:
        self.suggest.setEnabled(True)
        self.suggest.setText(self.suggest_label)
        if error:
            self.suggest.setToolTip(f"Ollama could not help: {error}")
            self.suggest.setText("Ollama unavailable — try again")
            return
        self.full.setPlainText(replace_section(self.full.toPlainText(), subject, assessment))
        self.tabs.setCurrentIndex(0)

    def _mark(self) -> None:
        self.marked = True
        self.mark.setEnabled(False)
        self.mark.setText(self.marked_label)
