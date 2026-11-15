"""Tools → Fetch Missed News (GDELT)…: news from a stretch when S2 Watch wasn't saving.

GDELT's news index can be searched for a past window; the other feeds only give what is
current. The window defaults to the longest recent stretch with little news saved
(``store.news_gap``). The fetch runs in the background, one request every 6 seconds, and the
stories found are handed to the OSINT source, which treats them like any other news.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QDateTime, QTimer, QTimeZone
from PySide6.QtWidgets import (
    QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QLabel, QProgressBar, QVBoxLayout,
)

from s2_watch.adapters.osint import GDELT_PAUSE_S, backfill_requests, fetch_gdelt_window

LONGEST = timedelta(days=7)      # GDELT's article search reaches back about three months; keep it modest


def _qt(value: datetime) -> QDateTime:
    return QDateTime.fromSecsSinceEpoch(int(value.timestamp()), QTimeZone.utc())


def _py(value: QDateTime) -> datetime:
    return datetime.fromtimestamp(value.toSecsSinceEpoch(), timezone.utc)


class BackfillDialog(QDialog):
    """Choose the missed stretch, fetch it, and hand the stories to ``deliver(messages)``, which
    returns how many were new."""

    def __init__(self, gap, deliver, parent=None, fetch=fetch_gdelt_window, now: datetime | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fetch missed news (GDELT)")
        self.deliver = deliver
        self.fetch = fetch
        now = now or datetime.now(timezone.utc)
        start, end = gap if gap else (now - timedelta(hours=6), now)
        self.intro = QLabel(
            ("S2 Watch saved little news from <b>{}</b> to <b>{}</b> (UTC): it wasn't running or saving, or the feeds "
             "were down. ").format(start.strftime("%d %b %H:%M"), end.strftime("%d %b %H:%M")) if gap else
            "No gap found in the last week's news. Choose a stretch to fetch anyway. ")
        self.intro.setText(self.intro.text() + "GDELT's news index can be searched for a past stretch (the other feeds "
                           "only give what is current): unrest and violence, failing infrastructure, and air travel "
                           "stopped. The stories are analysed and grouped like live news.")
        self.intro.setWordWrap(True)
        self.start = QDateTimeEdit(_qt(start))
        self.end = QDateTimeEdit(_qt(end))
        for edit in (self.start, self.end):
            edit.setDisplayFormat("dd MMM yyyy HH:mm 'UTC'")
            edit.setTimeZone(QTimeZone.utc())
            edit.setMaximumDateTime(_qt(now))
            edit.dateTimeChanged.connect(self._estimate)
        self.estimate = QLabel()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.fetch_button = self.buttons.addButton("Fetch", QDialogButtonBox.ButtonRole.ActionRole)
        self.fetch_button.setDefault(True)
        self.fetch_button.clicked.connect(self.start_fetch)
        self.buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("From", self.start)
        form.addRow("To", self.end)
        layout = QVBoxLayout(self)
        layout.addWidget(self.intro)
        layout.addLayout(form)
        layout.addWidget(self.estimate)
        layout.addWidget(self.progress)
        layout.addWidget(self.result)
        layout.addWidget(self.buttons)
        self._state: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)
        self._estimate()
        self.resize(520, 0)

    def window(self) -> tuple[datetime, datetime]:
        return _py(self.start.dateTime()), _py(self.end.dateTime())

    def _estimate(self, *_args) -> None:
        start, end = self.window()
        if end <= start:
            self.estimate.setText("<b>To</b> must be after <b>From</b>.")
            self.fetch_button.setEnabled(False)
            return
        if end - start > LONGEST:
            self.estimate.setText("At most a week at a time.")
            self.fetch_button.setEnabled(False)
            return
        requests = backfill_requests(start, end)
        minutes = max(1, round(requests * GDELT_PAUSE_S / 60))
        self.estimate.setText(f"{requests} requests to GDELT, one every {GDELT_PAUSE_S} seconds: about {minutes} "
                              f"minute{'s' if minutes != 1 else ''}.")
        self.fetch_button.setEnabled(self._thread is None)

    def start_fetch(self) -> None:
        start, end = self.window()
        self._state = {"done": 0, "total": backfill_requests(start, end), "messages": None, "error": ""}
        self.progress.setRange(0, self._state["total"])
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.fetch_button.setEnabled(False)
        self.start.setEnabled(False)
        self.end.setEnabled(False)
        self.result.setText("Fetching…")

        def run() -> None:
            try:
                self._state["messages"] = self.fetch(start, end, progress=self._progress, stopped=self._stop.is_set)
            except Exception as error:    # GDELT busy or unreachable
                self._state["error"] = str(error)[:160] or type(error).__name__
                self._state["messages"] = []

        self._thread = threading.Thread(target=run, name="gdelt-backfill", daemon=True)
        self._thread.start()
        self._timer.start()

    def _progress(self, done: int, total: int) -> None:
        self._state["done"] = done

    def _poll(self) -> None:
        self.progress.setValue(self._state.get("done", 0))
        if self._state.get("messages") is None:
            return
        self._timer.stop()
        self._thread = None
        messages = self._state["messages"]
        added = self.deliver(messages) if messages else 0
        error = self._state.get("error")
        text = (f"Found {len(messages)} stories; {added} new to S2 Watch, now being analysed and grouped."
                if messages else "No stories found.")
        if error:
            text += f" GDELT stopped answering ({error}); try the rest later."
        self.result.setText(text)
        self.progress.setValue(self.progress.maximum())

    def finished_fetch(self) -> bool:
        return self._thread is None and self._state.get("messages") is not None

    def reject(self) -> None:
        self._stop.set()     # a fetch in progress ends after its current request
        super().reject()
