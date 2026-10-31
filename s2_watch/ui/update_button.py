"""The update button beside the clock: orange when a newer S2 Watch is released; clicking it
then installs the update (S2 Watch closes, installs and reopens)."""

from __future__ import annotations

import os
import threading
from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QToolButton

from s2_watch import REPOSITORY_URL, __version__, updates

AVAILABLE_STYLE = ("QToolButton { color: white; background: #ef6c00; font-weight: bold; border-radius: 3px; "
                   "padding: 1px 8px; } QToolButton:hover { background: #f57c00; }")


class UpdateButton(QToolButton):
    """Checks GitHub shortly after start and every 6 hours; ``window`` is the main window."""

    checked = Signal(object, object, str)    # newest version or None, its changes, error
    downloaded = Signal(object, str)         # the unpacked version's folder or None, error

    def __init__(self, window, first_check_ms: int = 10_000) -> None:
        super().__init__(window)
        self.window = window
        self.available: str | None = None
        self.notes: list[str] = []
        self._busy = False
        self.checked.connect(self._checked)
        self.downloaded.connect(self._downloaded)
        self.clicked.connect(self._clicked)
        self._show("Up to date", f"S2 Watch {__version__}. Click to check for updates now.")
        self._timer = QTimer(self)
        self._timer.setInterval(updates.CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check)
        if first_check_ms >= 0:
            QTimer.singleShot(first_check_ms, self.check)
            self._timer.start()
        self._report_last_update()

    def _show(self, text: str, tip: str, highlight: bool = False) -> None:
        self.setText(text)
        self.setToolTip(tip)
        self.setStyleSheet(AVAILABLE_STYLE if highlight else "")

    def _report_last_update(self) -> None:
        store = self.window.store
        pending = store.get_setting("updates.pending", "")
        outcome = updates.update_outcome(pending)
        if outcome:
            store.set_setting("updates.pending", "")
        if outcome == "updated":
            self.window.statusBar().showMessage(f"Updated to S2 Watch {__version__}.", 120_000)
        elif outcome == "failed":
            self.window.statusBar().showMessage(
                f"The update to {pending} didn't install; still running {__version__}. "
                f"Details: {self.log_path()}", 600_000)

    def log_path(self) -> Path:
        return Path(self.window.database_path).resolve().parent / "update.log"

    def check(self) -> None:
        if self._busy:
            return
        self._busy = True
        if not self.available:
            self._show("Checking…", "Looking for a newer S2 Watch")

        def work() -> None:
            try:
                latest = updates.latest_version()
                newer = latest if updates.newer(latest) else None
                self.checked.emit(newer, updates.changes(newer) if newer else [], "")
            except Exception as error:  # offline, GitHub busy: say so, try again later
                self.checked.emit(None, [], str(error))

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, version, notes, error: str) -> None:
        self._busy = False
        if error:
            if not self.available:
                self._show("Updates?", f"Couldn't check for updates ({error}). Click to try again.")
            return
        self.available, self.notes = version, list(notes)
        if version:
            self._show(f"Update to {version}", f"S2 Watch {version} is available (you have {__version__}). "
                                               "Click to install it.", highlight=True)
        else:
            self._show("Up to date", f"S2 Watch {__version__} is the latest. Click to check again.")

    def _clicked(self) -> None:
        if not self.available:
            self.check()
            return
        kind = updates.install_kind()
        if kind != "installer":
            self._update_by_hand(kind)
            return
        notes = "".join(f"<li>{escape(line)}</li>" for line in self.notes[:12])
        answer = QMessageBox.question(
            self.window, "Update S2 Watch",
            f"<p>Install S2 Watch <b>{self.available}</b>? You have {__version__}.</p>"
            + (f"<p>What's new:</p><ul>{notes}</ul>" if notes else "")
            + "<p>S2 Watch downloads the update, closes, installs it (about a minute) and opens again. Your data and "
              "settings are kept, and a copy of the database is saved first.</p>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._busy = True
        self.setEnabled(False)
        self._show(f"Downloading {self.available}…", "S2 Watch closes and installs it once it's downloaded",
                   highlight=True)
        version = self.available

        def work() -> None:
            try:
                self.downloaded.emit(updates.download(version), "")
            except Exception as error:
                self.downloaded.emit(None, str(error))

        threading.Thread(target=work, daemon=True).start()

    def _downloaded(self, source, error: str) -> None:
        self._busy = False
        self.setEnabled(True)
        if source is None:
            self._show(f"Update to {self.available}", f"The download failed ({error}). Click to try again.",
                       highlight=True)
            QMessageBox.warning(self.window, "Update S2 Watch", f"The update couldn't be downloaded:\n\n{error}")
            return
        self.window.store.set_setting("updates.pending", self.available)
        try:
            updates.start_updater(source, Path(self.window.database_path).resolve(), self.available, os.getpid(),
                                  self.log_path())
        except OSError as error:
            self.window.store.set_setting("updates.pending", "")
            QMessageBox.warning(self.window, "Update S2 Watch", f"The installer couldn't be started:\n\n{error}")
            return
        self.window.close()

    def _update_by_hand(self, kind: str) -> None:
        release = f"{REPOSITORY_URL}/releases/tag/v{self.available}"
        if kind == "checkout":
            text = (f"S2 Watch {self.available} is available. This S2 Watch runs from a git clone: run "
                    "<code>git pull</code> in it, then restart S2 Watch.")
        else:
            text = (f"S2 Watch {self.available} is available. This copy wasn't set up by the installer, so it "
                    f"can't update itself: download it from <a href='{release}'>{release}</a> and run its installer.")
        box = QMessageBox(QMessageBox.Icon.Information, "Update S2 Watch", text, parent=self.window)
        if kind != "checkout":
            box.addButton("Open the download page", QMessageBox.ButtonRole.AcceptRole).clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(release)))
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
