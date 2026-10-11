from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.analysis.base import AnalysisResult
from s2_watch.events import Place, incident_key, key_terms, report_hazards
from s2_watch.models import Message
from s2_watch.monitoring import message_key
from s2_watch.storage import SQLiteStore

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
TULSA = Place(36.154, -95.993, "city", "OK", 6)
SAND_SPRINGS = Place(36.140, -96.109, "city", "OK", 6)       # 10 km away
OKC = Place(35.468, -97.516, "city", "OK", 6)                 # 160 km away


def add(store, application, text, when, place, category="Climate / Environment", fields=None, summary=None):
    message = Message(application, text, received_at=when, fields=fields or {})
    result = AnalysisResult(True, category, "high", summary or text, 0.8)
    message_id = store.save_message(message, message_key(message))
    store.save_analysis(message_id, result)
    report = store.assign_report(message_id, message, result, state=place.state, region=place.region)
    return store.assign_event(report.report_id, category, report_hazards(text), place, result.summary,
                              incident_key(message), key_terms(text))


def test_radio_and_official_reports_of_one_flood_are_one_event(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    radio = add(store, "MagCommstat", "TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA)
    js8 = add(store, "JS8Call", "W5ABC: @MAGNET FLASH FLOOD SAND SPRINGS, WATER OVER ROAD", T0 + timedelta(hours=2), SAND_SPRINGS)
    official = add(store, "OSINT", "FEMA: flood declaration Oklahoma", T0 + timedelta(hours=5), TULSA,
                   fields={"sender": "FEMA"})
    fire = add(store, "MagCommstat", "TULSA OK - GRASS FIRE NEAR HIGHWAY", T0 + timedelta(hours=1), TULSA)
    far = add(store, "MagCommstat", "OKC FLOODING ON I-40", T0 + timedelta(hours=1), OKC)
    later = add(store, "MagCommstat", "TULSA FLOODING AGAIN", T0 + timedelta(days=4), TULSA)
    assert radio == js8 == official
    assert len({radio, fire, far, later}) == 4        # other hazard, too far, days later
    [event] = [e for e in store.events_since(T0 - timedelta(days=1)) if e.event_id == radio]
    assert event.reports == 3 and set(event.sources) == {"MagCommstat", "JS8Call", "OSINT"}
    assert event.official == "FEMA" and event.radio_lead_minutes == 300   # radio 5 hours ahead
    assert [r["source"] for r in store.event_reports(radio)] == ["MagCommstat", "JS8Call", "OSINT"]


def test_named_fires_are_separate_events_even_when_close(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    near = Place(43.18, -118.92, "precise", "OR", 10)
    other = Place(43.05, -118.96, "precise", "OR", 10)          # 15 km apart
    stubble = {"type": "WILDFIRE", "state": "OR"}
    first = add(store, "OSINT", "NIFC wildfire: Stubble Fire — Oregon; 15,000 acres", T0, near, fields=stubble)
    update = add(store, "OSINT", "NIFC wildfire: Stubble Fire — Oregon; 15,673 acres, 93% contained",
                 T0 + timedelta(days=6), near, fields=stubble, summary="Stubble Fire — Coordinates in report · 15,673 acres")
    neighbour = add(store, "OSINT", "NIFC wildfire: Jackass Butte Fire — Oregon; 24,724 acres", T0, other, fields=stubble)
    assert first == update != neighbour                   # a week of updates, one fire
    title = store.connection.execute("SELECT title FROM events WHERE id = ?", (first,)).fetchone()[0]
    assert title == "Stubble Fire — Oregon · 15,673 acres"   # latest update, placeholder replaced


def test_stories_without_a_hazard_need_the_same_details(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    dallas = Place(32.82, -96.84, "city", "TX", 6)
    one = add(store, "OSINT", "Ex-Dallas officer Amber Guyger granted parole after murder of Botham Jean", T0, dallas, "Crime")
    two = add(store, "OSINT", "Ex-Dallas police officer Amber Guyger granted parole in Botham Jean murder | NBC 5",
              T0 + timedelta(hours=3), dallas, "Crime")
    other = add(store, "OSINT", "Dallas police search for gunman after stray bullet hits child | NBC 5",
                T0 + timedelta(hours=1), dallas, "Crime")
    assert one == two != other


def test_hazard_words_match_whole_words() -> None:
    assert report_hazards("Dallas police say child was shot") == set()
    assert report_hazards("Wildfire smoke and brush fires") == {"fire", "air"}
    assert report_hazards("Freezing rain, snowfall") == {"winter"}
    assert report_hazards("Presidential motorcade") == set()     # security isn't an event hazard
    assert "ARKANSAS" not in key_terms("Tehran says US response in hand | The Arkansas Democrat-Gazette")


def test_a_report_later_judged_routine_leaves_its_event(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = add(store, "MagCommstat", "HOW OFTEN DO YOU FEEL THE EARTHQUAKES AROUND YOUR AREA?", T0, TULSA)
    assert [e.event_id for e in store.events_since(T0 - timedelta(hours=1))] == [event_id]
    message = Message("MagCommstat", "HOW OFTEN DO YOU FEEL THE EARTHQUAKES AROUND YOUR AREA?", received_at=T0)
    message_id = store.save_message(message, message_key(message))
    store.assign_report(message_id, message, AnalysisResult(False, "routine", "low", "chat", 0.9))   # Ollama's verdict
    assert store.events_since(T0 - timedelta(hours=1)) == [] and store.event_reports(event_id) == []
