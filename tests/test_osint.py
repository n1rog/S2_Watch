from datetime import datetime, timedelta, timezone

from s2_watch.adapters import osint
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.models import Message


def wait_for(adapter, attempts=50):
    import time
    for _ in range(attempts):
        messages = adapter.read_messages()
        if messages:
            return messages
        time.sleep(0.02)
    return []


def test_adapter_delivers_each_item_once_within_the_review_window(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    items = [Message("OSINT", "Protest at city hall in Austin, TX", received_at=now - timedelta(hours=2),
                     fields={"type": "NEWS", "message_id": "u1"}),
             Message("OSINT", "Old riot story", received_at=now - timedelta(days=40), fields={"type": "NEWS", "message_id": "u2"})]
    monkeypatch.setattr(osint, "_CACHE", {})
    adapter = osint.OsintAdapter(timedelta(days=30), fetchers={"gdelt": lambda: items})
    try:
        delivered = wait_for(adapter)
        assert [m.fields["message_id"] for m in delivered] == ["u1"]
        adapter._collect("gdelt", lambda: items)   # fetched again: nothing new
        assert adapter.read_messages() == []
        assert adapter.status.startswith("Connected")
    finally:
        adapter.close()


def test_a_failing_source_is_reported_without_stopping_the_others(monkeypatch) -> None:
    monkeypatch.setattr(osint, "_CACHE", {})

    def broken():
        raise OSError("HTTP Error 429")

    adapter = osint.OsintAdapter(None, fetchers={"gdelt": broken, "faa": lambda: [
        Message("OSINT", "FAA security flight restriction (TFR 6/5774): Dallas, TX", fields={"message_id": "t"})]})
    try:
        assert len(wait_for(adapter)) == 1
        assert "failing: gdelt" in adapter.status
    finally:
        adapter.close()


def test_osint_items_are_classified() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    tfr = analyzer.analyze(Message("OSINT", "FAA security flight restriction (TFR 6/5774): Dallas, TX, Thursday, October 1"))
    assert (tfr.newsworthy, tfr.category) == (True, "Politics")
    fema = analyzer.analyze(Message("OSINT", "FEMA Fire Management declaration FM-5677: Hydra Fire — Texas; incident type Fire; 1 area designated"))
    assert fema.category == "Climate / Environment"
    covid = analyzer.analyze(Message("OSINT", "FEMA Emergency declaration EM-9999: Covid-19 — Ohio; incident type Biological; 88 areas designated"))
    assert (covid.category, covid.urgency) == ("Health / Wellness", "high")
