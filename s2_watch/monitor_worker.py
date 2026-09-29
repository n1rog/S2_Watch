"""Background polling, so slow sources, backlogs and AI analysis never freeze the window.

The worker lives on its own QThread with its own SQLite connection. Each second it
reads the adapters, matches watch forms, analyses and saves matches, and works out
where each message is on the map (including any network geocoding). Results reach
the window in batches through the ``batch_ready`` signal.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.analysis.summary import is_placeholder_summary, summarize
from s2_watch.geocoding import NominatimGeocoder
from s2_watch.locating import LocationResolver, ResolvedLocation
from s2_watch.models import Message, WatchForm
from s2_watch.monitoring import Monitor
from s2_watch.overlays import CoverageTracker
from s2_watch.storage import ReportInfo, SQLiteStore

BATCH_SIZE = 200


@dataclass
class MonitorConfig:
    adapters: list = field(default_factory=list)
    forms: list[WatchForm] = field(default_factory=list)
    analyzer: NewsAnalyzer | None = None
    geocoding: bool = False
    # Show the saved reports seen since this time first, before the sources are read.
    preload_since: datetime | None = None
    preload_limit: int = 1000


@dataclass
class ProcessedMessage:
    message: Message
    result: AnalysisResult | None
    locations: list[ResolvedLocation]
    report: ReportInfo | None = None
    preloaded: bool = False   # from the database at launch, not read from a source just now


class MonitorWorker(QObject):
    batch_ready = Signal(object)  # list[ProcessedMessage]
    coverage_ready = Signal(object)  # list[dict] from CoverageTracker.snapshot()

    def __init__(self, database_path: str) -> None:
        super().__init__()
        self.database_path = database_path
        self.store: SQLiteStore | None = None
        self.monitor: Monitor | None = None
        self.adapters: list = []
        self.resolver = LocationResolver()
        self._batch: list[ProcessedMessage] = []
        self.coverage = CoverageTracker()
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        self.store = SQLiteStore(self.database_path)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(1000)
        self._coverage_timer = QTimer(self)
        self._coverage_timer.timeout.connect(self._emit_coverage)
        self._coverage_timer.start(30_000)

    @Slot(object)
    def configure(self, config: MonitorConfig) -> None:
        for adapter in self.adapters:
            if not any(adapter is new for new in config.adapters):
                adapter.close()
        self.adapters = list(config.adapters)
        self.resolver.geocoder = (
            NominatimGeocoder(self.store.get_geocode, self.store.set_geocode)
            if config.geocoding and self.store else None
        )
        self.monitor = Monitor(
            self.adapters, config.forms, self.store, self._matched, config.analyzer, self._analysed,
            self._saved, self.coverage.add,
        ) if self.adapters and self.store else None
        if self.store and config.preload_since is not None:
            self._preload(config)

    def _preload(self, config: MonitorConfig) -> None:
        """Send the saved reports in the watch period, so the window doesn't depend on the
        sources still having them (a feeder offline, a revised OSINT item, a restart)."""
        forms = [form for form in config.forms if form.enabled]
        batch = []
        for report_id, message, result in self.store.recent_reports(config.preload_since, config.preload_limit):
            if result is None or not any(form.matches(message) for form in forms):
                continue
            locations = self.resolver.resolve(message)
            batch.append(ProcessedMessage(message, result, locations, self.store.report_info(report_id), preloaded=True))
        if batch:
            batch.reverse()   # oldest first, like a source's backlog
            self.batch_ready.emit(batch)

    @Slot()
    def poll(self) -> None:
        if not self.monitor:
            return
        try:
            self.monitor.poll()
        finally:
            self._flush()
            if self.coverage.changed and self.coverage.cells and not getattr(self, "_coverage_sent", False):
                self._emit_coverage()  # show the first coverage right after the backlog, not 30 s later

    def _emit_coverage(self) -> None:
        if self.coverage.changed:
            self._coverage_sent = True
            self.coverage_ready.emit(self.coverage.snapshot())

    def request_stop(self) -> None:
        """Called from the window's thread so a poll in progress ends at the next message.

        Used before shutting down and before a re-scan, which re-reads everything anyway.
        """
        monitor = self.monitor
        if monitor:
            monitor.cancelled = True

    @Slot()
    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
            self._coverage_timer.stop()
        for adapter in self.adapters:
            adapter.close()
        self.adapters = []
        self.monitor = None
        if self.store:
            self.store.close()
            self.store = None

    def _matched(self, message: Message) -> None:
        # A whole digest gets no markers of its own: its items carry their own places.
        locations = [] if message.fields.get("digest_parent") else self.resolver.resolve(message)
        self._batch.append(ProcessedMessage(message, None, locations))
        if self.monitor and self.monitor.analyzer is None and len(self._batch) >= BATCH_SIZE:
            self._flush()

    def _saved(self, message_id: int, message: Message, result: AnalysisResult | None) -> None:
        if not self._batch or self._batch[-1].message is not message:
            return
        processed = self._batch[-1]
        if result is not None and is_placeholder_summary(message, result.summary):
            # The offline analyzer's summary repeats the message; write a headline instead
            # (an AI analyzer's own summary is kept).
            result = replace(result, summary=summarize(message, result, processed.locations))
            self.store.save_analysis(message_id, result)
        processed.result = result
        # The report's area: the most specific location's state and MARS region.
        area = next((location for location in processed.locations if location.state or location.region), None)
        processed.report = self.store.assign_report(
            message_id, message, result,
            state=area.state or "" if area else "",
            region=area.region if area else None,
        )

    def _analysed(self, message: Message, result: AnalysisResult) -> None:
        # _saved has already stored the result, with its headline summary.
        if self._batch and self._batch[-1].message is message and self._batch[-1].result is None:
            self._batch[-1].result = result
        if len(self._batch) >= BATCH_SIZE:
            self._flush()

    def _flush(self) -> None:
        if self._batch:
            batch, self._batch = self._batch, []
            self.batch_ready.emit(batch)
