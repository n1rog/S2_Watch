"""Draft a follow-up or a briefing: edit the full report, then copy or save its FLMSG and
JS8Call versions (made from the full report as it is edited)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from s2_watch.analysis.ollama import draft_assessment
from s2_watch.followup import JS8CALL_TARGET, FollowUpFacts, dtg, flmsg_text, full_report, js8call_text
from s2_watch.s2_report import file_stem, parse_report


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
    suggestion_ready = Signal(str, str)   # the report with the suggestion in, error (from a thread)

    def __init__(self, facts: FollowUpFacts | None, store, parent=None, ollama: tuple[str, str] | None = None, *,
                 text: str | None = None, js8=js8call_text, title: str = "Draft follow-up report",
                 file_prefix: str = "S2-FOLLOWUP", suggest_fn=draft_assessment,
                 suggest_label: str = "Suggest subject && assessment (Ollama)",
                 mark_label: str = "Mark report “Follow-up created”", marked_label: str = "Marked “Follow-up created”",
                 callsign: str = "", export: bool = False, draft_kind: str = "",
                 draft_id: int | None = None, report_id: int | None = None) -> None:
        super().__init__(parent)
        self.ollama = ollama   # (endpoint, model) when Ollama is the analysis mode
        self.setWindowTitle(title)
        self.resize(940, 700)
        self.facts = facts
        self.store = store
        self.marked = False
        self.js8 = js8
        self.file_prefix = file_prefix
        self.suggest_fn = suggest_fn
        self.suggest_label = suggest_label
        self.marked_label = marked_label
        # Saving inside S2 Watch: "briefing" or "report"; empty = not offered.
        self.draft_kind = draft_kind
        self.draft_id = draft_id
        self.report_id = report_id
        self._saved_text: str | None = None
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
            "Write the full report first; the FLMSG and JS8Call versions are made from it as you edit. "
            "In the Word and web versions, \"time | event\" lines become the timeline, \"category | HIGH\" lines the "
            "probability table, and lines in (parentheses) are prompts to replace.",
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
        if export:   # the MAGNET S2 report as a Word document or a web page
            word = buttons.addButton("Save Word report…", QDialogButtonBox.ButtonRole.ActionRole)
            word.setToolTip("Save in the MAGNET S2 Intelligence Report format (.docx)")
            word.clicked.connect(lambda: self._export("docx"))
            page = buttons.addButton("Save web page…", QDialogButtonBox.ButtonRole.ActionRole)
            page.setToolTip("Save as a web page in the MAGNET template (for the website)")
            page.clicked.connect(lambda: self._export("html"))
        if draft_kind:
            self.save_button = buttons.addButton("Save in S2 Watch", QDialogButtonBox.ButtonRole.ActionRole)
            self.save_button.setToolTip("Keep this draft in S2 Watch (Reports → Saved Reports & Briefings); "
                                        "saving again updates it")
            self.save_button.clicked.connect(self.save_draft)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)
        self._derive()
        if draft_id is not None:
            self._saved_text = self.full.toPlainText()   # reopened: nothing unsaved yet
        self._update_save_button()
        self.full.textChanged.connect(self._update_save_button)

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

        self.full.setReadOnly(True)   # the suggestion is written into this text
        self._update_save_button()     # (saved once it has arrived)

        def work() -> None:
            try:
                result = self.suggest_fn(report, endpoint, model)
                # A whole updated report, or (subject, assessment) for the older drafts.
                text = result if isinstance(result, str) else replace_section(report, *result)
                self.suggestion_ready.emit(text, "")
            except Exception as error:
                self.suggestion_ready.emit("", str(error))

        threading.Thread(target=work, daemon=True, name="s2-watch-assessment").start()

    def _suggested(self, text: str, error: str) -> None:
        self.full.setReadOnly(False)
        self._update_save_button()
        self.suggest.setEnabled(True)
        self.suggest.setText(self.suggest_label)
        if error:
            self.suggest.setToolTip(f"Ollama could not help: {error}")
            self.suggest.setText("Ollama unavailable — try again")
            return
        self.full.setPlainText(text)
        self.tabs.setCurrentIndex(0)

    def _remember_magcon(self) -> None:
        magcon = parse_report(self.full.toPlainText()).fields.get("MAGCON STATUS", "").strip()
        if magcon:
            self.store.set_setting("magcon.status", magcon)   # the next report starts from it

    def _export(self, kind: str) -> None:
        report = parse_report(self.full.toPlainText())
        name = f"{file_stem(report.subject, report.dtg)}.{kind}"
        filters = "Word documents (*.docx)" if kind == "docx" else "Web pages (*.html)"
        path, _ = QFileDialog.getSaveFileName(self, "Save MAGNET S2 report", str(Path.home() / name), filters)
        if not path:
            return
        if kind == "docx":
            from s2_watch.s2_docx import write_docx
            write_docx(report, path)
        else:
            from s2_watch.s2_html import write_html
            write_html(report, path)
        self._remember_magcon()

    # -- saving inside S2 Watch ---------------------------------------------------------

    def unsaved(self) -> bool:
        return bool(self.draft_kind) and self.full.toPlainText() != self._saved_text

    def save_draft(self) -> None:
        text = self.full.toPlainText()
        self.draft_id = self.store.save_draft(self.draft_kind, text, self.draft_id, self.report_id)
        self._saved_text = text
        self._remember_magcon()
        self._update_save_button()

    def _update_save_button(self) -> None:
        if not self.draft_kind:
            return
        if self.unsaved():
            self.save_button.setText("Save in S2 Watch")
            self.save_button.setEnabled(not self.full.isReadOnly())
        else:
            self.save_button.setText("Saved in S2 Watch ✓")
            self.save_button.setEnabled(False)

    def done(self, result: int) -> None:
        # Closing with unsaved changes asks first (Close button, window close, Escape).
        if self.unsaved() and not self.full.isReadOnly():
            answer = QMessageBox.question(
                self, "Unsaved draft", "Save this draft in S2 Watch before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save:
                self.save_draft()
        super().done(result)

    def closeEvent(self, event) -> None:
        self.done(QDialog.DialogCode.Accepted)
        event.ignore() if self.isVisible() else event.accept()

    def _mark(self) -> None:
        self._remember_magcon()
        if self.draft_kind:
            self.save_draft()   # what was sent (or followed up) is kept
        self.marked = True
        self.mark.setEnabled(False)
        self.mark.setText(self.marked_label)
