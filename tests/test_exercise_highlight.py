"""Exercise traffic is hard to miss: purple rows, a badge, a counter by the clock, a review item."""

from datetime import datetime, timedelta, timezone

from s2_watch.analysis.base import AnalysisResult
from s2_watch.events import Place, key_terms
from s2_watch.exercise import COLOUR
from s2_watch.models import Message

NOW = datetime.now(timezone.utc)


def add_exercise(store, text="**** EXERCIZE **** POWER AND COMMUNICATIONS FAILURE IN HORSESHOE BAY"):
    message = Message("FLMSG", text, received_at=NOW - timedelta(hours=1), fields={"sender": "KT5DC"})
    message_id = store.save_message(message, text)
    result = AnalysisResult(True, "Infrastructure", "medium", "EXERCISE: Power failure in Horseshoe Bay", 0.9, ())
    store.save_analysis(message_id, result)
    report = store.assign_report(message_id, message, result, state="TX", region=6)
    event_id = store.assign_event(report.report_id, "Infrastructure", {"power"}, Place(30.54, -98.37, "city", "TX", 6),
                                  text, "", key_terms(text))
    return message, report, event_id


def test_an_exercise_event_is_badged_and_tinted_in_the_list(main_window) -> None:
    window = main_window()
    window.store.set_setting("exercise.in_progress", "1")
    _message, _report, event_id = add_exercise(window.store)
    window.events_view.refresh()
    table = window.events_view.table
    title = table.item(0, table.COLUMNS.index("Event"))
    assert title.text().startswith("EXERCISE · ") and title.font().bold()
    assert table.item(0, table.COLUMNS.index("Concern")).text() == "Exercise"
    assert table.item(0, 0).background().color().name() == COLOUR
    window.events_view.exercise_only.setChecked(True)
    assert [event.event_id for event in window.events_view.table.events] == [event_id]


def test_the_clock_counts_exercise_messages_during_an_exercise(main_window) -> None:
    window = main_window()
    window.store.set_setting("exercise.in_progress", "1")
    _message, _report, event_id = add_exercise(window.store)
    window.update_exercise_marker()
    assert not window.exercise_label.isHidden() and window.exercise_label.text() == "EXERCISE · 1"
    window.exercise_label.click()
    assert window.tabs.currentWidget() is window.events_view and window.events_view.exercise_only.isChecked()
    assert window.events_view.detail.current.event_id == event_id


def test_outside_an_exercise_its_traffic_is_hidden_everywhere(main_window) -> None:
    from s2_watch.analysis.base import AnalysisResult as Result
    from s2_watch.locating import ResolvedLocation
    from s2_watch.monitor_worker import ProcessedMessage

    window = main_window()
    window.exercise_action.setChecked(True)
    message, report, event_id = add_exercise(window.store)
    window.exercise_action.setChecked(False)                     # Exercise in Progress off
    window.update_exercise_marker()
    assert window.exercise_label.isHidden()                      # no EXERCISE button
    window.review_view.refresh()
    assert not [item for item in window.review_view.items if item.kind == "exercise"]
    window.events_view.refresh()
    assert event_id not in [event.event_id for event in window.events_view.table.events]
    assert not window.events_view.exercise_only.isEnabled()
    window.refresh_map_events()
    assert event_id not in [event.event_id for event in window.map_events.events]
    window.show_analysis(message, Result(False, "routine", "low", "EXERCISE: Power failure", 0.9, ()))
    assert window.analysis.isRowHidden(0)                         # the Reports list
    place = [ResolvedLocation("city", 30.54, -98.37, "Horseshoe Bay, TX", "TX", 6)]
    window.map_view.add_message(message, place)
    assert not [location for location in window.map_view._visible_locations() if location.get("exercise")]
    notices = len(window.notices)
    window.exercise_received(ProcessedMessage(message, None, [], report), report)
    assert len(window.notices) == notices                        # not announced
    window.exercise_action.setChecked(True)                      # back on: shown again
    assert not window.analysis.isRowHidden(0) and not window.exercise_label.isHidden()
    assert window.map_view._show_exercise              # (switching re-reads the sources, which redraws the map)


def test_exercise_traffic_waits_in_needs_review_and_is_announced(main_window) -> None:
    from s2_watch.monitor_worker import ProcessedMessage

    window = main_window()
    window.store.set_setting("exercise.in_progress", "1")          # during an exercise: listed by event
    message, report, event_id = add_exercise(window.store)
    window.review_view.refresh()
    [item] = [item for item in window.review_view.items if item.kind == "exercise"]
    assert item.target == event_id and item.label == "Exercise traffic"
    result = AnalysisResult(True, "Infrastructure", "medium", "EXERCISE: Power failure", 0.9, ())
    window.exercise_received(ProcessedMessage(message, result, [], report), report)
    assert window.notices[0][1].startswith("Exercise traffic from KT5DC: EXERCISE: Power failure")
    assert window.notices[0][2] == event_id


def test_an_exercise_marker_is_flagged_on_the_map(main_window) -> None:
    from s2_watch.locating import ResolvedLocation

    window = main_window()
    drill = Message("JS8Call", "*** EXERCISE *** BRIDGE OUT IN TULSA")
    real = Message("JS8Call", "BRIDGE OUT IN TULSA")
    place = [ResolvedLocation("city", 36.15, -95.99, "Tulsa, OK", "OK", 6)]
    window.map_view.add_message(drill, place)
    window.map_view.add_message(real, place)
    flags = [location["exercise"] for location in window.map_view._locations]
    assert flags == [True, False]


def test_an_exercise_digest_is_one_message_however_many_events(main_window) -> None:
    from s2_watch.digest import split_digest
    from tests.test_accuracy import SNAPSHOT

    window = main_window()
    store = window.store
    store.set_setting("exercise.in_progress", "1")
    digest = Message("FLMSG", SNAPSHOT.format(heading="EXERCISE "), received_at=NOW - timedelta(hours=1),
                     fields={"filename": "MAGNET_S2_RR_261108-1800Z_WEEKLY_SNAPSHOT.sig (copy).b2s"})
    places = [Place(40.9, -73.8, "city", "NY", 2), Place(37.27, -107.01, "city", "CO", 8),
              Place(38.9, -77.0, "city", "DC", 3), Place(37.4, -122.1, "city", "CA", 9)]
    categories = ["Politics", "Climate / Environment", "Health / Wellness", "Infrastructure"]
    ids = []
    for index, (item, place, category) in enumerate(zip(split_digest(digest), places, categories)):
        message_id = store.save_message(item, f"item{index}")
        result = AnalysisResult(True, category, "medium", item.raw_content[:40], 0.9, ())
        report = store.assign_report(message_id, item, result, state=place.state, region=place.region)
        ids.append(store.assign_event(report.report_id, category, set(), place, item.raw_content, "",
                                      key_terms(item.raw_content)))
    assert len(set(ids)) == 4
    window.review_view.refresh()
    [item] = [item for item in window.review_view.items if item.kind == "exercise"]
    assert item.title.startswith("Exercise digest — ") and item.target[0] == "events" and set(item.target[1]) == set(ids)
    assert window.exercise_label.text() == "EXERCISE · 4"           # its events: what a click lists
    window.review_act(item, "primary")
    assert window.events_view.exercise_only.isChecked()
    assert set(window.events_view.table.selected_events()) == set(ids)


def test_the_file_name_is_shown_for_events_and_reports(main_window) -> None:
    window = main_window()
    message, report, event_id = add_exercise(window.store)
    named = Message("FLMSG", message.raw_content, received_at=message.received_at,
                    fields={"sender": "KT5DC", "filename": "KT5DC-20261115-002234Z-10.k2s"})
    window.store.save_message(named, "second copy")
    window.store.assign_report(window.store.find_message_id("second copy"), named, None)
    window.open_event(event_id)
    assert "File: KT5DC-20261115-002234Z-10.k2s" in window.events_view.detail.browser.toPlainText()
    result = AnalysisResult(True, "Infrastructure", "medium", "Power failure", 0.9, ())
    window.show_analysis(named, result)
    column = window.ANALYSIS_COLUMNS.index("File")
    assert window.analysis.item(0, column).text() == "KT5DC-20261115-002234Z-10.k2s"


def test_the_button_counts_the_exercise_events_a_click_lists(main_window) -> None:
    from s2_watch.digest import split_digest
    from tests.test_accuracy import SNAPSHOT

    window = main_window()
    store = window.store
    store.set_setting("exercise.in_progress", "1")
    add_exercise(store)                                   # one message, one event
    digest = Message("FLMSG", SNAPSHOT.format(heading="EXERCISE "), received_at=NOW - timedelta(hours=1),
                     fields={"filename": "MAGNET_S2_RR_WEEKLY_SNAPSHOT (copy).b2s"})
    places = [Place(40.9, -73.8, "city", "NY", 2), Place(37.27, -107.01, "city", "CO", 8),
              Place(38.9, -77.0, "city", "DC", 3), Place(37.4, -122.1, "city", "CA", 9)]
    for index, (item, place) in enumerate(zip(split_digest(digest), places)):   # one message, four events
        message_id = store.save_message(item, f"item{index}")
        result = AnalysisResult(True, "Politics", "medium", item.raw_content[:40], 0.9, ())
        report = store.assign_report(message_id, item, result, state=place.state, region=place.region)
        store.assign_event(report.report_id, "Politics", set(), place, item.raw_content, "", key_terms(item.raw_content))
    test = Message("JS8Call", "EXERCISE EXERCISE EXERCISE THIS IS A TEST OF THE MAGNET S2 SYSTEM",
                   received_at=NOW - timedelta(hours=1))       # a test: a message with no event
    message_id = store.save_message(test, "test")
    store.assign_report(message_id, test, AnalysisResult(False, "routine", "low", "test", 0.9, ()))
    window.events_view.refresh()
    assert window.exercise_label.text() == "EXERCISE · 5"         # the events, not the 3 messages
    assert "3 exercise messages" in window.exercise_label.toolTip()
    window.exercise_label.click()
    assert len(window.events_view.table.events) == 5
