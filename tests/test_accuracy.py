"""Exercise traffic, events that close, and sender track records."""

from datetime import timedelta
from pathlib import Path

from s2_watch.analysis.base import AnalysisResult
from s2_watch.events import EventSummary, Place, all_clear, incident_key, key_terms, report_hazards
from s2_watch.exercise import BANNER, exercise_kind, mark_exercise
from s2_watch.models import Message
from s2_watch.monitoring import message_key
from s2_watch.s2_report import briefing_report, source_lines
from s2_watch.storage import SQLiteStore

from tests.test_events import T0, TULSA, add

RESULT = AnalysisResult(True, "Infrastructure", "high", "Bridge out", 0.8)


def radio(text: str, application: str = "JS8Call") -> Message:
    return Message(application, text)


# -- exercise traffic ---------------------------------------------------------------------

def test_only_clear_markers_make_exercise_or_test_traffic() -> None:
    assert exercise_kind(radio("W1AW: @MAGNET EXERCISE EXERCISE EXERCISE DAM FAILURE AT LAKE X")) == "exercise"
    assert exercise_kind(radio("THIS IS A DRILL. BRIDGE COLLAPSE ON I-35")) == "exercise"
    assert exercise_kind(radio("EXERCISE: TORNADO DAMAGE IN TULSA")) == "exercise"
    assert exercise_kind(radio("NOT REAL WORLD - POWER OUT")) == "exercise"
    assert exercise_kind(radio("THIS IS A TEST OF THE NET")) == "test"
    # Real traffic, and ordinary words, stay real.
    assert exercise_kind(radio("REAL WORLD: EXERCISE SUSPENDED, ACTUAL TORNADO IN TULSA")) == ""
    assert exercise_kind(radio("NO DUFF NO DUFF EXERCISE EXERCISE INJURED PERSON AT SITE")) == ""
    assert exercise_kind(radio("Report actual events (not exercise or test)")) == ""
    assert exercise_kind(radio("BREVITY TEST 9AAIAAAB")) == ""
    assert exercise_kind(Message("OSINT", "State Dept travel advisory: Kenya - Level 1: Exercise Normal Precautions")) == ""


def test_exercise_traffic_is_news_only_during_an_exercise() -> None:
    message = radio("EXERCISE EXERCISE EXERCISE BRIDGE OUT ON RT 9")
    quiet = mark_exercise(message, RESULT, exercise_in_progress=False)
    assert not quiet.newsworthy and quiet.summary == "EXERCISE: Bridge out"
    during = mark_exercise(message, RESULT, exercise_in_progress=True)
    assert during.newsworthy and during.category == "Infrastructure" and during.summary == "EXERCISE: Bridge out"
    test = mark_exercise(radio("THIS IS A TEST, BRIDGE OUT"), RESULT, exercise_in_progress=True)
    assert not test.newsworthy                                 # test traffic is never news
    assert mark_exercise(radio("BRIDGE OUT ON RT 9"), RESULT, False) is RESULT


def test_exercise_events_are_marked_in_briefings(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = add(store, "MagCommstat", "EXERCISE EXERCISE TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA)
    [event] = store.events_since(T0 - timedelta(hours=1))
    assert event.event_id == event_id and event.exercise
    text = briefing_report([event], T0 + timedelta(hours=1), 24)
    lines = text.splitlines()
    assert lines[0] == BANNER and lines[-1] == BANNER
    assert "SUBJECT: EXERCISE: Situation Briefing" in text


# -- events that close ----------------------------------------------------------------------

def test_all_clear_words_close_and_negations_dont() -> None:
    assert all_clear("POWER HAS BEEN RESTORED TO MOST OF TULSA") == "power has been restored"
    assert all_clear("TORNADO WARNING CANCELLED FOR OSAGE COUNTY") == "warning cancelled"
    assert all_clear("I-35 BRIDGE REOPENED") == "bridge reopened"
    assert all_clear("POWER NOT YET RESTORED IN TULSA") == ""
    assert all_clear("NO ALL CLEAR YET") == ""
    assert all_clear("RIVER FLOODING, ROADS CLOSED") == ""


def place_report(store, text, when, closing=True):
    message = Message("MagCommstat", text, received_at=when)
    result = AnalysisResult(True, "Infrastructure", "high", text, 0.8)
    message_id = store.save_message(message, message_key(message))
    report = store.assign_report(message_id, message, result, state="OK", region=6)
    return store.assign_event(report.report_id, "Infrastructure", report_hazards(text), TULSA, text,
                              incident_key(message), key_terms(text), text if closing else "")


def test_an_all_clear_report_closes_its_event_and_news_reopens_it(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = place_report(store, "TULSA OK - POWER OUTAGE ACROSS NORTH TULSA", T0)
    assert place_report(store, "TULSA OK - POWER HAS BEEN RESTORED, OUTAGE OVER", T0 + timedelta(hours=3)) == event_id
    [event] = store.events_since(T0 - timedelta(hours=1))
    assert event.status(T0 + timedelta(hours=4)) == "closed" and event.close_reason == "power has been restored"
    assert event.closed_by == "report"
    place_report(store, "TULSA OK - POWER OUTAGE AGAIN NORTH TULSA", T0 + timedelta(hours=5))
    [event] = store.events_since(T0 - timedelta(hours=1))
    assert event.closed_at is None                           # it wasn't over after all


def test_the_operator_closes_and_reopens_events_and_quiet_ones_show(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = place_report(store, "TULSA OK - POWER OUTAGE ACROSS NORTH TULSA", T0)
    [event] = store.events_since(T0 - timedelta(hours=1))
    assert event.status(T0 + timedelta(hours=1)) == "active"
    assert event.status(T0 + timedelta(hours=13)) == "quiet" and event.status_text(T0 + timedelta(hours=13)) == "Quiet 13 h"
    store.close_event(event_id, "all clear from EOC", when=T0 + timedelta(hours=2))
    [event] = store.events_since(T0 - timedelta(hours=1))
    assert event.status_text(T0) == "Closed: all clear from EOC" and event.closed_by == "operator"
    store.reopen_event(event_id)
    [event] = store.events_since(T0 - timedelta(hours=1))
    assert event.closed_at is None


def test_briefings_list_closed_events_as_resolved() -> None:
    now = T0 + timedelta(hours=6)
    open_event = EventSummary(1, "Flooding — Tulsa, OK", "Climate / Environment", "OK", 6, T0, T0 + timedelta(hours=5),
                              2, ("MagCommstat",), "high", "", None)
    closed = EventSummary(2, "Power outage — Tulsa, OK", "Infrastructure", "OK", 6, T0, T0 + timedelta(hours=3),
                          2, ("MagCommstat",), "high", "", None, closed_at=T0 + timedelta(hours=3),
                          close_reason="power has been restored", closed_by="report")
    text = briefing_report([open_event, closed], now, 24)
    situation = text.split("SITUATION", 1)[1].split("Other Activity:", 1)[0]
    assert "Flooding — Tulsa, OK:" in situation and "Power outage" not in situation
    assert "Resolved in the Period:" in text and "- Power outage — Tulsa, OK: power has been restored" in text
    assert "1 event closed in the period." in text


# -- sender track records ------------------------------------------------------------------

def test_track_records_suggest_a_grade_once_enough_reports_are_judged(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    for hour in range(5):          # five flood reports from K1ABC, each confirmed by FEMA
        when = T0 + timedelta(days=3 * hour)
        add(store, "MagCommstat", f"TULSA OK - RIVER FLOODING ROUND {hour}", when, TULSA, fields={"sender": "K1ABC"})
        add(store, "OSINT", f"FEMA: flood declaration Oklahoma {hour}", when + timedelta(hours=1), TULSA,
            fields={"sender": "FEMA"})
    add(store, "MagCommstat", "TULSA OK - RIVER FLOODING", T0, TULSA, fields={"sender": "w2xyz"})
    records = store.sender_track_records()
    assert records["K1ABC"].confirmed == 5 and records["K1ABC"].grade == "B"
    assert records["W2XYZ"].grade == "F" and "too few" in records["W2XYZ"].explanation   # upper-cased call sign
    assert "FEMA" not in records                               # official sources aren't radio stations
    reports = [{"source": "MagCommstat", "sender": "K1ABC", "first_seen": T0.isoformat(), "text": "x"},
               {"source": "MagCommstat", "sender": "W2XYZ", "first_seen": T0.isoformat(), "text": "y"}]
    lines = source_lines(reports, confirmed=True, grades={"K1ABC": "B"})
    assert "[B1]" in lines[0] and "[F1]" in lines[1]


def test_the_operator_sets_a_stations_trust_level(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    add(store, "MagCommstat", "TULSA OK - RIVER FLOODING", T0, TULSA, fields={"sender": "w2xyz"})
    store.set_sender_trust("w2xyz", "A")                     # known for years: completely reliable
    store.set_sender_trust("N0NEW", "D")                     # not heard yet, rated ahead
    records = store.sender_track_records()
    assert records["W2XYZ"].grade == "A" and records["W2XYZ"].suggested == "F"
    assert "set by you" in records["W2XYZ"].explanation and records["N0NEW"].grade == "D"
    reports = [{"source": "MagCommstat", "sender": "W2XYZ", "first_seen": T0.isoformat(), "text": "y"}]
    assert "[A1]" in source_lines(reports, confirmed=True, grades={"W2XYZ": records["W2XYZ"].grade})[0]
    store.set_sender_trust("W2XYZ", "")                      # back to Automatic
    assert store.sender_track_records()["W2XYZ"].grade == "F" and "N0NEW" in store.sender_trust()


def test_the_track_records_dialog_sets_trust_levels(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from s2_watch.ui.senders_dialog import SendersDialog

    QApplication.instance() or QApplication([])
    store = SQLiteStore(tmp_path / "s2.db")
    add(store, "MagCommstat", "TULSA OK - RIVER FLOODING", T0, TULSA, fields={"sender": "K1ABC"})
    dialog = SendersDialog(store, str)
    column = dialog.COLUMNS.index("Trust level")
    assert dialog.table.item(0, column).text() == "Automatic"
    dialog.set_trust(["K1ABC"], "B")
    dialog.refresh()
    assert store.sender_trust() == {"K1ABC": "B"} and dialog.table.item(0, column).text() == "B — usually reliable"
    dialog.deleteLater()


def test_the_monitor_keeps_exercise_traffic_off_the_news_until_an_exercise(tmp_path: Path) -> None:
    from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
    from s2_watch.models import WatchForm
    from s2_watch.monitoring import Monitor

    class Feeder:
        def read_messages(self):
            return [Message("MagCommstat", "EXERCISE EXERCISE EXERCISE TULSA OK - TORNADO DAMAGE, POWER OUTAGE",
                            received_at=T0)]

    store = SQLiteStore(tmp_path / "s2.db")
    saved = []

    def run(exercise_in_progress: bool):
        saved.clear()
        Monitor([Feeder()], [WatchForm("MagCommstat", "All", "")], store, lambda _m: None, RuleBasedNewsAnalyzer(),
                on_saved=lambda message_id, message, result: saved.append(
                    (store.assign_report(message_id, message, result), result)),
                exercise_in_progress=exercise_in_progress).poll()
        return saved[0]

    report, result = run(False)
    assert not result.newsworthy and result.summary.startswith("EXERCISE")
    assert store.connection.execute("SELECT newsworthy, exercise FROM reports").fetchone() == (0, "exercise")
    report, result = run(True)                                     # read again during an exercise
    assert result.newsworthy
    assert store.connection.execute("SELECT newsworthy FROM reports").fetchone() == (1,)


def test_events_already_over_close_from_history_but_not_from_news(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    outage = place_report(store, "TULSA OK - POWER OUTAGE ACROSS NORTH TULSA", T0, closing=False)
    place_report(store, "TULSA OK - POWER HAS BEEN RESTORED, OUTAGE OVER", T0 + timedelta(hours=3), closing=False)
    shooting = add(store, "OSINT", "Shooting in Tulsa; suspect arrested", T0, TULSA, category="Crime",
                   fields={"sender": "tulsaworld.com"})
    assert store.close_events_from_history(T0 - timedelta(days=1)) == 1
    closed = {e.event_id: e.closed_at is not None for e in store.events_since(T0 - timedelta(days=1))}
    assert closed[outage] and not closed[shooting]              # a news headline doesn't close a story


def test_the_quiet_threshold_is_a_setting() -> None:
    from s2_watch import events

    summary = EventSummary(1, "Flooding", "Climate / Environment", "OK", 6, T0, T0, 1, ("MagCommstat",), "high", "", None)
    try:
        events.set_quiet_hours(4)
        assert summary.status(T0 + timedelta(hours=5)) == "quiet"
        events.set_quiet_hours(12)
        assert summary.status(T0 + timedelta(hours=5)) == "active"
    finally:
        events.set_quiet_hours(12)


def test_the_status_column_moves_next_to_last_activity_once(tmp_path: Path) -> None:
    from s2_watch.ui.events_view import EventsTable

    store = SQLiteStore(tmp_path / "s2.db")
    table = EventsTable(str, store=store, state_key="events.map.header")
    assert table.horizontalHeader().visualIndex(table.COLUMNS.index("Status")) == 1
    table.horizontalHeader().moveSection(1, 5)                 # the operator moves it elsewhere
    table._save_header()
    again = EventsTable(str, store=store, state_key="events.map.header")
    assert again.horizontalHeader().visualIndex(again.COLUMNS.index("Status")) == 5
