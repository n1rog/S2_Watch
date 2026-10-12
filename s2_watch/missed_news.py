"""Background check of radio traffic the watch forms didn't keep, for news they missed.

Watch forms keep only messages with news keywords, so news worded differently is never
seen. With Ollama available, this reads that other radio traffic slowly in a background
thread, newest first and each message only once, and lists what the model thinks is news
as "possible misses" for the operator to add as a report or dismiss. It stays out of the
way: after each model call it waits twice as long as the call took (at most a third of
the time), and it waits while the computer is busy.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Signal

from s2_watch.analysis.base import AnalysisResult
from s2_watch.analysis.ollama import OllamaError, OllamaNewsAnalyzer, readable_text
from s2_watch.dedup import report_text
from s2_watch.models import Message
from s2_watch.storage import SQLiteStore

MIN_CONFIDENCE = 0.6
MAX_PENDING = 5000
# Traffic that is never news: heartbeats, calls, signal reports, acknowledgements, greetings.
ROUTINE = re.compile(r"^(?:HB|HEARTBEAT|CQ|SNR|ACK|QSL|QSO|73|GM|GA|GE|GN|TU|RR|R|ROGER|INFO|GRID|HEARING|QUERY|"
                     r"STATUS|AGN\??|\?)\b", re.IGNORECASE)


def check_key(message: Message) -> str:
    """One key per distinct message text, so copies relayed by several feeders are read once."""
    return hashlib.sha1(report_text(readable_text(message)).encode("utf-8")).hexdigest()


FRAGMENT = re.compile(r"\[partial|fragment(?:ed)? by js8call|\bincomplete message\b", re.IGNORECASE)


def is_fragment(text: str) -> bool:
    """JS8Call marks messages it received only in part; a model invents the rest."""
    return bool(FRAGMENT.search(text))


def worth_checking(message: Message) -> bool:
    if message.source_application == "OSINT":
        return False          # official items are all kept and judged by the rules already
    body = readable_text(message)
    return (len(body) >= 20 and len(body.split()) >= 4 and not ROUTINE.match(body)
            and not is_fragment(message.raw_content))


def computer_busy() -> bool:
    """True when the processors are mostly in use (Linux and macOS; Windows: never)."""
    try:
        load, _five, _fifteen = os.getloadavg()
    except (AttributeError, OSError):
        return False
    return load > 0.75 * (os.cpu_count() or 1)


class MissedNewsScanner(QObject):
    found = Signal()          # a possible miss was recorded (the tab refreshes)
    progress = Signal(int, int)   # checked this session, waiting

    def __init__(self, database_path: str, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.enabled = False
        self.analyzer: OllamaNewsAnalyzer | None = None
        self.last_error = ""
        self._pending: deque[Message] = deque(maxlen=MAX_PENDING)
        self._keys: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.checked = 0

    def configure(self, enabled: bool, model: str, endpoint: str, examples=None) -> None:
        self.analyzer = OllamaNewsAnalyzer(model=model, endpoint=endpoint) if enabled else None
        if self.analyzer is not None:
            self.analyzer.examples = list(examples or [])
        self.enabled = enabled
        if enabled and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="s2-watch-missed-news")
            self._thread.start()

    def offer(self, message: Message) -> None:
        """Called from the polling thread for each message no watch form kept."""
        if not worth_checking(message):
            return
        key = check_key(message)
        with self._lock:
            if key in self._keys:
                return
            self._keys.add(key)
            self._pending.append(message)   # newest last; taken from the end

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def stop(self) -> None:
        self._stop.set()

    def _next(self) -> Message | None:
        with self._lock:
            return self._pending.pop() if self._pending else None

    def _open_store(self) -> SQLiteStore | None:
        # At startup the window and the polling thread set up the same database; a
        # simultaneous set-up can find it briefly locked, so try again rather than give up.
        for _attempt in range(30):
            try:
                return SQLiteStore(self.database_path)
            except sqlite3.OperationalError:
                if self._stop.wait(2):
                    return None
        return None

    def _run(self) -> None:
        store = self._open_store()
        if store is None:
            self.last_error = "could not open the database"
            return
        try:
            while not self._stop.is_set():
                try:
                    self._check_one(store)
                except sqlite3.OperationalError as error:   # the database briefly busy
                    self.last_error = str(error)
                    self._stop.wait(10)
        finally:
            store.close()

    def _check_one(self, store: SQLiteStore) -> None:
        """Read the newest waiting message (if the check is on and the computer free)."""
        analyzer = self.analyzer
        if not self.enabled or analyzer is None or computer_busy():
            self._stop.wait(30)
            return
        message = self._next()
        if message is None:
            self._stop.wait(15)
            return
        key = check_key(message)
        if store.missed_checked(key):
            return
        started = time.monotonic()
        try:
            result = analyzer.analyze(message)
        except OllamaError as error:
            if str(error).startswith(("Unable to contact", "Ollama returned HTTP")):
                self.last_error = str(error)   # Ollama stopped or busy: try again later
                with self._lock:
                    self._pending.append(message)
                self._stop.wait(120)
                return
            # The model answered but not usably (an unknown category...): record and move on.
            result = AnalysisResult(False, "routine", "low", f"(model reply unusable: {error})", 0.0)
        self.last_error = ""
        worth = result.newsworthy and result.confidence >= MIN_CONFIDENCE
        store.save_missed_check(key, message, result, "new" if worth else "routine")
        self.checked += 1
        self.progress.emit(self.checked, self.pending())
        if worth:
            self.found.emit()
        # Leave the computer free two thirds of the time.
        self._stop.wait(max(2.0, 2 * (time.monotonic() - started)))
