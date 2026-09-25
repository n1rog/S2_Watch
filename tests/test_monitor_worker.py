from PySide6.QtCore import QCoreApplication

from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.models import Message, WatchForm
from s2_watch.monitor_worker import BATCH_SIZE, MonitorConfig, MonitorWorker


class FakeAdapter:
    application_name = "JS8Call"

    def __init__(self, texts):
        self.pending = [Message("JS8Call", text, fields={"group": "@MR06"}) for text in texts]
        self.closed = False

    def read_messages(self):
        messages, self.pending = self.pending, []
        return messages

    def close(self):
        self.closed = True


def test_worker_matches_analyses_locates_and_batches(tmp_path) -> None:
    QCoreApplication.instance() or QCoreApplication([])
    worker = MonitorWorker(str(tmp_path / "s2.db"))
    worker.start()
    batches = []
    worker.batch_ready.connect(batches.append)
    adapter = FakeAdapter(["FLOODING IN TULSA, OK"] + ["net check"] * (BATCH_SIZE + 5))
    form = WatchForm("JS8Call", "All", "", enabled=True)
    worker.configure(MonitorConfig([adapter], [form], RuleBasedNewsAnalyzer()))
    worker.poll()

    assert [len(batch) for batch in batches] == [BATCH_SIZE, 6]
    first = batches[0][0]
    assert first.result.newsworthy and first.result.category == "Climate / Environment"
    assert [location.label for location in first.locations] == ["Tulsa, OK"]
    assert batches[0][1].locations[0].level == "region"  # "@MR06" only

    replacement = FakeAdapter([])
    worker.configure(MonitorConfig([replacement], [form], RuleBasedNewsAnalyzer()))
    assert adapter.closed and not replacement.closed
    worker.stop()
    assert replacement.closed


def test_request_stop_abandons_a_long_poll(tmp_path) -> None:
    QCoreApplication.instance() or QCoreApplication([])
    worker = MonitorWorker(str(tmp_path / "s2.db"))
    worker.start()
    seen = []
    adapter = FakeAdapter(["net check"] * 1000)
    worker.configure(MonitorConfig([adapter], [WatchForm("JS8Call", "All", "", enabled=True)], None))
    original = worker._matched

    def matched(message):
        original(message)
        seen.append(message)
        if len(seen) == 10:
            worker.request_stop()

    worker.monitor.on_match = matched
    worker.poll()
    assert len(seen) == 10
    worker.stop()
