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


def test_operator_keywords_and_decisions_feed_the_rules_and_the_model(tmp_path, monkeypatch) -> None:
    import s2_watch.analysis.ollama as ollama
    from s2_watch.analysis.ollama import OllamaNewsAnalyzer
    from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
    from s2_watch.missed_news import is_fragment

    storm = Message("MagCommstat", "TN - ISAIAS IMPACTS EXPECTED HERE TONIGHT.")
    assert not RuleBasedNewsAnalyzer().analyze(storm).newsworthy
    flagged = RuleBasedNewsAnalyzer.with_keywords({"Climate / Environment": ["isaias"]}).analyze(storm)
    assert flagged.newsworthy and flagged.category == "Climate / Environment" and flagged.matched_terms == ("isaias",)

    assert is_fragment("[Partial — fragmented by JS8Call]") and not worth_checking(
        Message("JS8Call", "W1AW: [Partial — fragmented by JS8Call] FLOODING ON MAIN ST NEAR THE BRIDGE"))

    prompts = []
    monkeypatch.setattr(ollama, "ollama_generate", lambda endpoint, model, prompt, timeout, json_format=False: (
        prompts.append(prompt), '{"newsworthy": true, "category": "Climate / Environment", "urgency": "medium", '
                                '"summary": "x", "confidence": 0.8, "matched_terms": ["isaias"]}')[1])
    model = OllamaNewsAnalyzer()
    model.examples = [("GOOD MORNING ALL", "not news"), ("ISAIAS IMPACTS TONIGHT", "news (Climate / Environment)")]
    model.analyze(storm)
    assert '- "GOOD MORNING ALL" -> not news' in prompts[0] and "news (Climate / Environment)" in prompts[0]


def test_triage_decisions_are_kept_and_old_finds_leave_the_list(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    store = SQLiteStore(tmp_path / "s2.db")
    now = datetime.now(timezone.utc)
    news = AnalysisResult(True, "Infrastructure", "medium", "Bridge out", 0.9, ("bridge out",))
    fresh = Message("JS8Call", "W5ABC: BRIDGE OUT ON HWY 9 NEAR ADA", received_at=now - timedelta(hours=2))
    old = Message("JS8Call", "W5ABC: ROAD WASHED OUT NEAR ADA", received_at=now - timedelta(days=3))
    store.save_missed_check("k1", fresh, news, "new")
    store.save_missed_check("k2", old, news, "new")
    since = now - timedelta(hours=24)
    [(key, _message, result)] = store.possible_misses("new", since)
    assert key == "k1" and result.matched_terms == ("bridge out",)
    corrected = AnalysisResult(True, "Infrastructure", "high", "Hwy 9 bridge out near Ada, OK", 0.9)
    store.set_missed_status("k1", "added", corrected=corrected)
    store.set_missed_status("k2", "dismissed")
    assert sorted(store.missed_decisions()) == [("BRIDGE OUT ON HWY 9 NEAR ADA", "news (Infrastructure)"),
                                                ("ROAD WASHED OUT NEAR ADA", "not news")]
    store.set_missed_status("k2", "deleted")
    assert store.possible_misses("dismissed") == [] and store.missed_checked("k2")   # not checked again


def test_the_add_form_suggests_the_model_s_word_and_returns_the_correction() -> None:
    from PySide6.QtWidgets import QApplication

    from s2_watch.ui.misses_view import AddMissDialog, suggested_keyword

    QApplication.instance() or QApplication([])
    storm = Message("MagCommstat", "TN - ISAIAS IMPACTS EXPECTED HERE TONIGHT.")
    guess = AnalysisResult(True, "Climate / Environment", "medium", "Isaias impacts expected tonight", 0.8, ("Isaias",))
    assert suggested_keyword(storm, guess) == "isaias"
    dialog = AddMissDialog(storm, guess)
    dialog.urgency.setCurrentText("high")
    dialog.headline.setText("Tropical Storm Isaias impacts expected in Tennessee tonight")
    corrected = dialog.corrected(0.8)
    assert (corrected.urgency, corrected.category, corrected.newsworthy) == ("high", "Climate / Environment", True)
    assert dialog.chosen_keyword() == "isaias"
