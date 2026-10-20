"""Single-source events, keyword noise, location corrections, and the time in a message."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.event_time import event_time
from s2_watch.locating import LocationResolver, message_hash
from s2_watch.models import Message
from s2_watch.storage import SQLiteStore

from tests.test_events import T0, TULSA, add


def test_an_unconfirmed_event_from_one_station_is_single_source(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    lone = add(store, "MagCommstat", "TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA, fields={"sender": "K1ABC"})
    add(store, "JS8Call", "TULSA OK - FLOODING ON RIVERSIDE DR", T0 + timedelta(minutes=30), TULSA,
        fields={"sender": "k1abc"})                              # the same station again: still one source
    [event] = [e for e in store.events_since(T0 - timedelta(hours=1)) if e.event_id == lone]
    assert event.independent_sources == 1
    assert not event.single_source(T0 + timedelta(hours=1))      # too soon to say
    assert event.single_source(T0 + timedelta(hours=3))
    add(store, "MagCommstat", "TULSA OK - FLOODING, WATER RISING", T0 + timedelta(hours=1), TULSA,
        fields={"sender": "W2XYZ"})                              # a second station
    [event] = [e for e in store.events_since(T0 - timedelta(hours=1)) if e.event_id == lone]
    assert event.independent_sources == 2 and not event.single_source(T0 + timedelta(hours=3))


def test_a_stopped_keyword_no_longer_makes_news_but_other_words_still_do() -> None:
    message = Message("JS8Call", "W1AW: NET OUTAGE TONIGHT, BLACKOUT IN TULSA")
    plain = RuleBasedNewsAnalyzer.with_keywords({})
    stopped = RuleBasedNewsAnalyzer.with_keywords({}, {"Infrastructure": ["outage", "blackout"]})
    assert "outage" in plain.analyze(message).matched_terms
    assert not stopped.analyze(Message("JS8Call", "W1AW: NET OUTAGE TONIGHT")).newsworthy
    assert RuleBasedNewsAnalyzer.with_keywords({}, {"Infrastructure": ["outage"]}).analyze(message).matched_terms \
        == ("blackout",)                                       # the next word in the same message still counts
    # A different set of words is a different analyzer, so saved results aren't reused.
    assert plain.name == "rules" and stopped.name.startswith("rules:") and stopped.name != \
        RuleBasedNewsAnalyzer.with_keywords({}, {"Infrastructure": ["outage"]}).name


def test_keyword_noise_counts_ignored_reports_by_word_and_origin(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    for hour in range(3):
        add(store, "MagCommstat", f"NET OUTAGE CHECK {hour}", T0 + timedelta(days=hour * 3), TULSA,
            category="Infrastructure")
    store.connection.execute("UPDATE analysis_results SET matched_terms = 'outage'")
    store.connection.execute("UPDATE reports SET status = 'ignored' WHERE id IN (SELECT id FROM reports LIMIT 2)")
    store.connection.commit()
    [row] = [r for r in store.keyword_noise() if r["keyword"] == "outage"]
    assert (row["reports"], row["ignored"], row["ignored_radio"], row["ignored_feeds"]) == (3, 2, 2, 0)


def test_a_corrected_place_is_used_for_that_phrase_or_that_message() -> None:
    resolver = LocationResolver()
    tulsa = resolver.resolve(Message("Manual", "Tulsa, OK"))[0]
    flooding = Message("JS8Call", "W5ABC: @MAGNET FLOODING IN BIXBY, WATER OVER ROAD")
    other = Message("JS8Call", "W5ABC: STORM DAMAGE NEAR SKIATOOK")
    resolver.set_corrections([("BIXBY", tulsa)])
    assert resolver.resolve(flooding) == [tulsa]
    resolver.set_corrections([(f"#msg:{message_hash(other)}", tulsa)])
    assert resolver.resolve(other) == [tulsa] and resolver.resolve(flooding) != [tulsa]


def test_correcting_a_report_moves_it_and_its_event(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = add(store, "MagCommstat", "RIVER FLOODING, ROADS CLOSED", T0, TULSA)
    report_id = store.event_reports(event_id)[0]["report_id"]
    okc = LocationResolver().resolve(Message("Manual", "Oklahoma City, OK"))[0]
    store.save_place_correction("RIVER FLOODING", okc)
    store.correct_report_location(report_id, okc)
    [report] = store.event_reports(event_id)
    assert (report["lat"], report["lon"], report["place_level"]) == (okc.lat, okc.lon, "city")
    assert store.place_corrections()[0][0] == "RIVER FLOODING"
    assert store.connection.execute("SELECT lat FROM events WHERE id = ?", (event_id,)).fetchone()[0] == okc.lat


def test_times_in_messages_are_read_but_forecasts_and_future_times_are_not() -> None:
    heard = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
    assert event_time("TORNADO TOUCHDOWN AT 1420Z NEAR BIXBY", heard) == heard.replace(hour=14, minute=20)
    assert event_time("DA[261016-0544Z] FLOODING", heard) == heard.replace(hour=5, minute=44)
    assert event_time("152330Z POWER OUT", heard) == datetime(2026, 10, 15, 23, 30, tzinfo=timezone.utc)
    assert event_time("POWER OUT SINCE 9:15 AM CDT", heard) == heard.replace(hour=14, minute=15)
    assert event_time("STORM EXPECTED AT 1800Z", heard) is None
    assert event_time("WARNING UNTIL 1645Z", heard) is None
    assert event_time("HEARD AT 2350Z", heard) is None                       # later today: not past
    assert event_time("HEARD AT 2350Z", datetime(2026, 10, 17, 0, 15, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 16, 23, 50, tzinfo=timezone.utc)                 # just before midnight
    assert event_time("GRID EM26 1500 WATTS", heard) is None
