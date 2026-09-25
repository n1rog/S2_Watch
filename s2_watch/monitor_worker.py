"""Background polling, so slow sources, backlogs and AI analysis never freeze the window.

The worker lives on its own QThread with its own SQLite connection. Each second it
reads the adapters, matches watch forms, analyses and saves matches, and works out
where each message is on the map (including any network geocoding). Results reach
the window in batches through the ``batch_ready`` signal.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.geocoding import NominatimGeocoder
from s2_watch.locating import LocationResolver, ResolvedLocation
from s2_watch.models import Message, WatchForm
from s2_watch.monitoring import Monitor
from s2_watch.storage import ReportInfo, SQLiteStore

BATCH_SIZE = 200


@dataclass
class MonitorConfig:
    adapters: list = field(default_factory=list)
    forms: list[WatchForm] = field(default_factory=list)
    analyzer: NewsAnalyzer | None = None
    geocoding: bool = False


@dataclass
class ProcessedMessage:
    message: Message
    result: AnalysisResult | None
    locations: list[ResolvedLocation]
    report: ReportInfo | None = None


class MonitorWorker(QObject):
    batch_ready = Signal(object)  # list[ProcessedMessage]

    def __init__(self, database_path: str) -> None:
        super().__init__()
        self.database_path = database_path
        self.store: SQLiteStore | None = None
        self.monitor: Monitor | None = None
        self.adapters: list = []
        self.resolver = LocationResolver()
        self._batch: list[ProcessedMessage] = []
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        self.store = SQLiteStore(self.database_path)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(1000)

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
            self._saved,
        ) if self.adapters and self.store else None

    @Slot()
    def poll(self) -> None:
        if not self.monitor:
            return
        try:
            self.monitor.poll()
        finally:
            self._flush()

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
        for adapter in self.adapters:
            adapter.close()
        self.adapters = []
        self.monitor = None
        if self.store:
            self.store.close()
            self.store = None

    def _matched(self, message: Message) -> None:
        self._batch.append(ProcessedMessage(message, None, self.resolver.resolve(message)))
        if self.monitor and self.monitor.analyzer is None and len(self._batch) >= BATCH_SIZE:
            self._flush()

    def _saved(self, message_id: int, message: Message, result: AnalysisResult | None) -> None:
        if not self._batch or self._batch[-1].message is not message:
            return
        processed = self._batch[-1]
        # The report's area: the most specific location's state and MARS region.
        area = next((location for location in processed.locations if location.state or location.region), None)
        processed.report = self.store.assign_report(
            message_id, message, result,
            state=area.state or "" if area else "",
            region=area.region if area else None,
        )

    def _analysed(self, message: Message, result: AnalysisResult) -> None:
        if self._batch and self._batch[-1].message is message:
            self._batch[-1].result = result
        if len(self._batch) >= BATCH_SIZE:
            self._flush()

    def _flush(self) -> None:
        if self._batch:
            batch, self._batch = self._batch, []
            self.batch_ready.emit(batch)
