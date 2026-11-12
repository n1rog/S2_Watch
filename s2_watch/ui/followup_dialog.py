"""Draft a follow-up or a briefing: edit the full report, then copy or save its FLMSG and
JS8Call versions (made from the full report as it is edited).

**Save** keeps the draft in S2 Watch and writes every format (full text, FLMSG, JS8Call, and
for MAGNET S2 reports the Word document and web page) to the day's reports folder
(s2_watch.report_files); **More** saves one format somewhere else."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from s2_watch.analysis.ollama import draft_assessment
from s2_watch.followup import JS8CALL_TARGET, FollowUpFacts, dtg, flmsg_text, full_report, js8call_text
from s2_watch import report_files
from s2_watch.s2_report import PRECEDENCE_LEVELS, file_stem, parse_report


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


def replace_field(report: str, key: str, value: str) -> str:
    """The report with its first "KEY: ..." routing line set to ``value``."""
    lines = report.splitlines()
    for index, line in enumerate(lines):
        if line.partition(":")[0].strip().upper() == key:
            lines[index] = f"{key}: {value}"
            break
    return "\n".join(lines)


class FollowUpDialog(QDialog):
    suggestion_ready = Signal(str, str)   # the report with the suggestion in, error (from a thread)

    def __init__(self, facts: FollowUpFacts | None, store, parent=None, ollama: tuple[str, str] | None = None, *,
                 text: str | None = None, js8=js8call_text, title: str = "Draft follow-up report",
                 file_prefix: str = "S2-FOLLOWUP", suggest_fn=draft_assessment,
                 suggest_label: str = "Suggest subject && assessment (Ollama)",
                 mark_label: str = "Mark report “Follow-up created”", marked_label: str = "Marked “Follow-up created”",
                 callsign: str = "", export: bool = False, draft_kind: str = "",
                 draft_id: int | None = None, report_id: int | None = None, event_id: int | None = None) -> None:
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
        self.event_id = event_id
        self.export = export            # MAGNET S2 report: Word and web versions too
        self._saved_text: str | None = None
        self._written_text: str | None = None     # the text the files in the reports folder hold
        self._pending_files: list[Path] = []      # written before the draft had an id
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.callsign = QLineEdit(facts.callsign if facts else callsign)
        self.callsign.setPlaceholderText("e.g. N1ROG")
        self.callsign.editingFinished.connect(self._callsign_changed)
        top = QFormLayout()
        top.addRow("Your callsign", self.callsign)
        self.precedence = QComboBox()
        for line, meaning, when in PRECEDENCE_LEVELS:           # flash, immediate, priority, routine
            self.precedence.addItem(line)
            self.precedence.setItemData(self.precedence.count() - 1, f"Inform others {when}. {meaning}",
                                        Qt.ItemDataRole.ToolTipRole)
        self.precedence.setToolTip("How quickly you must inform others about this event. S2 Watch starts from the event's "
                                   "urgency (never Flash); choose another level here (or edit the PRECEDENCE line).")
        self.precedence.activated.connect(self._precedence_chosen)
        top.addRow("Precedence", self.precedence)
        self.top = top

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
        self.tabs.addTab(self._page(
            "Plain upper-case text wrapped for radio — paste into an FLMSG form (e.g. ICS-213 or blank).",
            self.flmsg), "2. FLMSG")
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
        # One format, somewhere of your choosing (Save writes them all to the reports folder).
        self.more = QToolButton()
        self.more.setText("More")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self.more)
        if export:
            more.addAction("Save Word report as…").triggered.connect(lambda: self._save_as("docx"))
            more.addAction("Save web page as…").triggered.connect(lambda: self._save_as("html"))
        more.addAction("Save FLMSG text as…").triggered.connect(lambda: self._save_as("flmsg"))
        more.addAction("Save JS8Call text as…").triggered.connect(lambda: self._save_as("js8"))
        more.addAction("Save full report as text…").triggered.connect(lambda: self._save_as("text"))
        more.addSeparator()
        more.addAction("Open reports folder").triggered.connect(self._open_folder)
        self.more.setMenu(more)
        buttons.addButton(self.more, QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button = buttons.addButton("Save", QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.setDefault(True)
        self.save_button.setToolTip(
            ("Keep this draft in S2 Watch (the Reports tab) and write " if draft_kind else "Write ")
            + ("the full report, FLMSG and JS8Call text, Word report and web page" if export
               else "the full report, FLMSG and JS8Call text")
            + " to today's reports folder. Saving again updates them.")
        self.save_button.clicked.connect(self.save_all)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.close)
        self.saved_line = QLabel()
        self.saved_line.setTextFormat(Qt.TextFormat.RichText)
        self.saved_line.setWordWrap(True)
        self.saved_line.linkActivated.connect(lambda link: QDesktopServices.openUrl(QUrl.fromLocalFile(link)))
        self.saved_line.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs)
        layout.addWidget(self.saved_line)
        layout.addWidget(buttons)
        self._derive()
        if draft_id is not None:
            self._saved_text = self.full.toPlainText()   # reopened: nothing unsaved yet
            draft = store.get_draft(draft_id)
            if draft and draft["files"]:
                self._written_text = self._saved_text
                self._show_saved(Path(draft["files"][-1]).parent, len(draft["files"]), earlier=True)
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

    def _precedence_chosen(self) -> None:
        text = self.full.toPlainText()
        changed = replace_field(text, "PRECEDENCE", self.precedence.currentText())
        if changed != text:
            self.full.setPlainText(changed)

    def _show_precedence(self, report: str) -> None:
        """The picker follows the report's PRECEDENCE line (edited by hand too); hidden without one."""
        fields = parse_report(report).fields
        self.top.setRowVisible(self.precedence, "PRECEDENCE" in fields)
        value = fields.get("PRECEDENCE", "")
        index = next((i for i in range(self.precedence.count())
                      if self.precedence.itemText(i).split(" ")[0] == value.split(" ")[0].upper()), -1) if value else -1
        self.precedence.setCurrentIndex(index)

    def _derive(self) -> None:
        report = self.full.toPlainText()
        self._show_precedence(report)
        self.flmsg.setPlainText(flmsg_text(report))
        message = self.js8(report)
        self.js8call.setPlainText(message)
        over = len(message) > JS8CALL_TARGET
        self.js8call_count.setText(f"{len(message)} characters" + (" — consider shortening the subject" if over else ""))

    def _copy(self) -> None:
        editor = (self.full, self.flmsg, self.js8call)[self.tabs.currentIndex()]
        QGuiApplication.clipboard().setText(editor.toPlainText())

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

    # -- files ---------------------------------------------------------------------------

    def _stem(self) -> str:
        report = parse_report(self.full.toPlainText())
        if self.export or report.subject:
            return file_stem(report.subject, report.dtg)
        when = (self.facts.now or self.facts.message.received_at) if self.facts else datetime.now(timezone.utc)
        return f"{self.file_prefix}-" + dtg(when).replace(" ", "-")

    def _write_files(self) -> list[Path]:
        folder = report_files.day_folder(self.store)
        return report_files.save_all(folder, self._stem(), self.full.toPlainText(), self.flmsg.toPlainText(),
                                     self.js8call.toPlainText(), self.export)

    def _record_files(self, paths) -> None:
        if self.draft_id is not None:
            self.store.add_draft_files(self.draft_id, [*self._pending_files, *paths])
            self._pending_files = []
        else:
            self._pending_files.extend(paths)

    def _show_saved(self, folder: Path, count: int, earlier: bool = False) -> None:
        when = "Saved earlier" if earlier else f"Saved {count} files"
        self.saved_line.setText(f"{when} to {escape(str(folder))} — <a href=\"{escape(str(folder))}\">open folder</a>")
        self.saved_line.setVisible(True)

    def _open_folder(self) -> None:
        folder = report_files.reports_folder(self.store)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _save_as(self, kind: str) -> None:
        """One format, to a place the operator picks (starting where they last saved one)."""
        stem = self._stem()
        suffix, label, filters = {
            "docx": (".docx", "Save Word report", "Word documents (*.docx)"),
            "html": (".html", "Save web page", "Web pages (*.html)"),
            "flmsg": ("_FLMSG.txt", "Save FLMSG text", "Text files (*.txt)"),
            "js8": ("_JS8Call.txt", "Save JS8Call text", "Text files (*.txt)"),
            "text": (".txt", "Save full report", "Text files (*.txt)"),
        }[kind]
        start = self.store.get_setting(report_files.LAST_DIRECTORY_SETTING, "") or str(report_files.day_folder(self.store))
        path, _ = QFileDialog.getSaveFileName(self, label, str(Path(start) / f"{stem}{suffix}"), filters)
        if not path:
            return
        path = Path(path)
        if kind in ("docx", "html"):
            report = parse_report(self.full.toPlainText())
            if kind == "docx":
                from s2_watch.s2_docx import write_docx
                report_files.write_atomic(path, lambda temporary: write_docx(report, temporary))
            else:
                from s2_watch.s2_html import write_html
                report_files.write_atomic(path, lambda temporary: write_html(report, temporary))
        else:
            text = {"flmsg": self.flmsg, "js8": self.js8call, "text": self.full}[kind].toPlainText()
            report_files.write_text(path, text, "utf-8" if kind == "text" else "ascii")
        self.store.set_setting(report_files.LAST_DIRECTORY_SETTING, str(path.parent))
        self._record_files([path])
        self._remember_magcon()
        self.saved_line.setText(f"Saved {escape(path.name)} to {escape(str(path.parent))} — "
                                f"<a href=\"{escape(str(path.parent))}\">open folder</a>")
        self.saved_line.setVisible(True)

    # -- saving inside S2 Watch ---------------------------------------------------------

    def unsaved(self) -> bool:
        return bool(self.draft_kind) and self.full.toPlainText() != self._saved_text

    def save_draft(self) -> None:
        """Keep the draft in S2 Watch (no files)."""
        if not self.draft_kind:
            return
        text = self.full.toPlainText()
        self.draft_id = self.store.save_draft(self.draft_kind, text, self.draft_id, self.report_id, self.event_id)
        self._saved_text = text
        if self._pending_files:
            self._record_files([])
        self._remember_magcon()
        self._update_save_button()

    def save_all(self) -> None:
        """Save: keep the draft in S2 Watch and write every format to today's reports folder."""
        self.save_draft()
        try:
            paths = self._write_files()
        except OSError as error:
            QMessageBox.warning(self, "Not saved", f"The report files could not be written: {error}\n\n"
                                "Choose another reports folder under Configuration → Reports Folder…")
            return
        self._written_text = self.full.toPlainText()
        self._record_files(paths)
        self._remember_magcon()
        self._show_saved(paths[0].parent, len(paths))
        self._update_save_button()

    def _update_save_button(self) -> None:
        current = self.full.toPlainText()
        if self.unsaved() or current != self._written_text:
            self.save_button.setText("Save")
            self.save_button.setEnabled(not self.full.isReadOnly())
        else:
            self.save_button.setText("Saved ✓")
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
                self.save_all()
        super().done(result)

    def closeEvent(self, event) -> None:
        self.done(QDialog.DialogCode.Accepted)
        event.ignore() if self.isVisible() else event.accept()

    def _mark(self) -> None:
        self._remember_magcon()
        if self.draft_kind:
            self.save_draft()   # what was sent (or followed up) is kept
            self.store.mark_draft_sent(self.draft_id)
        self.marked = True
        self.mark.setEnabled(False)
        self.mark.setText(self.marked_label)
