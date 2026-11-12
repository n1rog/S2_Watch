"""One crime incident told in different words by different outlets is one event."""

from datetime import datetime, timedelta, timezone

from s2_watch.analysis.base import AnalysisResult
from s2_watch.events import Place, incident_marks, key_terms
from s2_watch.models import Message
from s2_watch.storage import SQLiteStore

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DC = Place(38.9015, -77.0114, "city", "DC", 3)
CHICAGO = Place(41.8781, -87.6298, "city", "IL", 5)
DEMING = Place(32.2687, -107.7586, "city", "NM", 6)


def test_marks_say_what_kind_of_crime_and_how_big() -> None:
    assert incident_marks("4 shot in Washington, DC as police respond to active shooting") == {
        "shooting", "count:4", "major"}
    assert incident_marks("Four shot in Washington, DC, shooting") == {"shooting", "count:4"}
    assert incident_marks("Shooting near DC fitness center leaves multiple people injured") == {"shooting", "count:many"}
    assert incident_marks("Fatal double shooting in Gwinnett County") == {"shooting", "count:2"}
    assert "past" in incident_marks("Truck driver sentenced for shooting into car")
    assert "past" in incident_marks("Estate sues bar following 2023 Bend shooting")
    assert "another" in incident_marks("Police report another early morning shooting in Spartanburg")
    assert incident_marks("Power outage in North Platte") == set()


def add(store, text, place, minutes, category="Crime"):
    message = Message("OSINT", text, received_at=NOW + timedelta(minutes=minutes), fields={"type": "NEWS"})
    message_id = store.save_message(message, text)
    result = AnalysisResult(True, category, "medium", text, 0.9, ())
    store.save_analysis(message_id, result)
    report = store.assign_report(message_id, message, result, state=place.state, region=place.region)
    return store.assign_event(report.report_id, category, set(), place, text, "", key_terms(text), "",
                              incident_marks(text))


def test_outlets_wording_one_shooting_differently_share_an_event(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    first = add(store, "Active shooting reported in Northwest DC; 4 shot", DC, 0)
    assert add(store, "Four shot in Washington, DC, shooting", DC, 5) == first
    assert add(store, "Shooting near DC fitness center leaves multiple people injured, officials say", DC, 20) == first
    assert add(store, "Police: 1 dead, at least 5 injured in Washington, DC shooting", DC, 90) == first   # the toll grows


def test_separate_shootings_in_a_city_stay_separate(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    first = add(store, "Chicago shooting: 2 teens wounded in Humboldt Park, police say", CHICAGO, 0)
    assert add(store, "Mother dies weeks after apparent road rage shooting in Chicago", CHICAGO, 60) != first
    assert add(store, "Chicago man charged in West Side shooting that prompted SWAT response", CHICAGO, 90) != first


def test_stories_naming_one_small_town_share_an_event(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    first = add(store, "Suspect killed in police shooting in Deming", DEMING, 0)
    assert add(store, "NMSP investigates deadly shooting by Deming police", DEMING, 120) == first
    assert add(store, "Police investigate another shooting in Deming", DEMING, 300) != first
    assert add(store, "Deming man sentenced in 2024 shooting", DEMING, 400) != first


def test_events_split_before_are_merged_once(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    ids = []
    for minutes, text in ((0, "Active shooting reported in Northwest DC; 4 shot"),
                          (5, "Four shot in Washington, DC, shooting")):
        message = Message("OSINT", text, received_at=NOW + timedelta(minutes=minutes), fields={"type": "NEWS"})
        message_id = store.save_message(message, text)
        result = AnalysisResult(True, "Crime", "medium", text, 0.9, ())
        report = store.assign_report(message_id, message, result, state="DC", region=3)
        ids.append(store.assign_event(report.report_id, "Crime", set(), DC, text, "", key_terms(text)))   # no marks: as before
    assert ids[0] != ids[1]
    merged = store.regroup_incidents(NOW - timedelta(days=1), lambda message, title: incident_marks(title))
    assert merged == 1
    assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone() == (1,)
    assert store.connection.execute("SELECT DISTINCT event_id FROM reports").fetchall() == [(ids[0],)]
