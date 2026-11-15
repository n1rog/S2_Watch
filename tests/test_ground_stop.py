"""One nationwide ground stop is one event; an airport's ground stops on different days are not."""

from datetime import datetime, timedelta, timezone

from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.events import Place, incident_marks, key_terms
from s2_watch.models import Message
from s2_watch.storage import SQLiteStore

T0 = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
NOWHERE = Place(None, None, "", "", None)


def place(store, text, when, where=NOWHERE, incident="", source="OSINT"):
    from s2_watch.analysis.base import AnalysisResult

    message = Message(source, text, received_at=when)
    message_id = store.save_message(message, text + when.isoformat())
    result = AnalysisResult(True, "Infrastructure", "medium", text, 0.9)
    report = store.assign_report(message_id, message, result)
    return store.assign_event(report.report_id, "Infrastructure", set(), where, text, incident, key_terms(text), "",
                              incident_marks(text))


def test_a_nationwide_stoppage_is_one_event_however_it_is_told(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    first = place(store, "ID FLIGHTRADAR SHOWING LOTS OF FEDEX UPS AIRCRAFT MOVING. GROUND STOP LIFTED?", T0,
                  Place(42.56, -114.54, "grid", "ID", 10), source="MagCommstat")
    later = [place(store, text, T0 + timedelta(hours=hours), where) for text, hours, where in (
        ("FAA blames Friday ground stop on equipment issue during telecoms network upgrade", 18, NOWHERE),
        ("Airlines work through cancellations, delays after nationwide ground stop", 19, NOWHERE),
        ("Toronto Pearson flights resume after FAA ground stop", 19, Place(43.7, -79.42, "city", "Canada", None)),
    )]
    assert set(later) == {first}
    assert store.event_by_id(first).title == "Nationwide ground stop"
    # Two days later, another stoppage is another event.
    assert place(store, "FAA halts all flights nationwide after computer outage", T0 + timedelta(days=3)) != first


def test_an_airports_ground_stops_on_different_days_are_separate(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    ewr = Place(40.69, -74.17, "precise", "NJ", 2)
    october = place(store, "FAA: ground stop at EWR airport — STAFFING, until 9:45 pm EDT", T0 - timedelta(days=21), ewr,
                    "airport:EWR")
    update = place(store, "FAA: ground stop at EWR airport — STAFFING, until 10:30 pm EDT",
                   T0 - timedelta(days=21) + timedelta(hours=1), ewr, "airport:EWR")
    november = place(store, "FAA: ground stop at EWR airport — STAFFING, until 11:00 pm EST", T0, ewr, "airport:EWR")
    assert update == october and november != october
    # A story about the nationwide stoppage doesn't join an airport's own status event.
    assert place(store, "FAA ground stop ends but passengers stuck at airport : NPR", T0 + timedelta(hours=2)) != november


def test_a_local_ground_stop_is_not_a_nationwide_one() -> None:
    assert incident_marks("FAA: ground stop at JFK airport — thunderstorms, until 8:00 pm EST") == set()
    assert "national:ground stop" in incident_marks("Ground stop at all US airports after FAA outage")
    rules = RuleBasedNewsAnalyzer()
    assert rules.analyze(Message("OSINT", "Airlines work through delays after nationwide ground stop")).urgency == "high"
    assert rules.analyze(Message("OSINT", "FAA: ground stop at JFK airport — thunderstorms")).urgency == "medium"
