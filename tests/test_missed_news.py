import time

from PySide6.QtCore import QCoreApplication

from s2_watch.analysis.base import AnalysisResult
from s2_watch.missed_news import MissedNewsScanner, check_key, worth_checking
from s2_watch.models import Message, WatchForm
from s2_watch.monitoring import Monitor
from s2_watch.storage import SQLiteStore

JS8 = '{"CMD": " MSG", "FREQ": 7117106, "TEXT": "W5ABC: @MAGNET MSG BRIDGE OUT ON HWY 9 NEAR ADA, CARS STRANDED"}'


def test_only_real_radio_messages_are_checked() -> None:
    assert worth_checking(Message("JS8Call", JS8))
    assert not worth_checking(Message("JS8Call", "W5ABC: @HB HEARTBEAT EM24"))
    assert not worth_checking(Message("JS8Call", "W5ABC: KD0XYZ SNR -12"))
    assert not worth_checking(Message("OSINT", "USGS: M2.1 earthquake somewhere quiet"))
    # Copies of one message relayed by two feeders are read once.
    assert check_key(Message("JS8Call", JS8)) == check_key(
        Message("JS8SuperSpotter", "W5ABC: @MAGNET MSG BRIDGE OUT ON HWY 9 NEAR ADA, CARS STRANDED"))


def test_monitor_hands_on_messages_no_form_kept(tmp_path) -> None:
    class Source:
        application_name = "JS8Call"

        def read_messages(self):
            return [Message("JS8Call", "FLOODING IN TULSA"), Message("JS8Call", "BRIDGE OUT ON HWY 9")]

    unmatched = []
    Monitor([Source()], [WatchForm("JS8Call", "Floods", "flood")], SQLiteStore(tmp_path / "s2.db"),
            lambda message: None, on_unmatched=unmatched.append).poll()
    assert [message.raw_content for message in unmatched] == ["BRIDGE OUT ON HWY 9"]


def test_scanner_records_what_the_model_thinks_is_news(tmp_path, monkeypatch) -> None:
    import s2_watch.missed_news as missed

    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(missed, "computer_busy", lambda: False)

    class FakeModel:
        def analyze(self, message, hint=None):
            news = "BRIDGE" in message.raw_content
            return AnalysisResult(news, "Infrastructure" if news else "routine", "high" if news else "low",
                                  "Bridge out on Hwy 9 near Ada" if news else "chat", 0.9)

    scanner = MissedNewsScanner(str(tmp_path / "s2.db"))
    scanner.configure(True, "test", "http://127.0.0.1:1")
    scanner.analyzer = FakeModel()
    scanner.offer(Message("JS8Call", JS8))
    scanner.offer(Message("JS8Call", "W1AW: KD0XYZ GOOD MORNING, NICE WEATHER FOR A WALK TODAY"))
    store = SQLiteStore(tmp_path / "s2.db")
    deadline = time.monotonic() + 15
    while sum(store.missed_counts().values()) < 2 and time.monotonic() < deadline:
        time.sleep(0.2)
    scanner.stop()
    assert store.missed_counts() == {"new": 1, "routine": 1}
    [(key, message, result)] = store.possible_misses()
    assert result.summary == "Bridge out on Hwy 9 near Ada" and message.source_application == "JS8Call"
    store.set_missed_status(key, "dismissed")
    assert store.possible_misses() == []
