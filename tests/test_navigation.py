"""Alerts, the tray icon and map markers take the operator to the event (usability review P1, P2, P9)."""

from datetime import datetime, timedelta, timezone

from s2_watch.analysis.base import AnalysisResult
from s2_watch.event_assessment import local_endpoint
from s2_watch.events import Place, key_terms
from s2_watch.models import Message

OLD = datetime.now(timezone.utc) - timedelta(days=10)


def add_event(store, text="Water main break in Pickens", when=OLD, urgency="high", official=False):
    fields = {"type": "NEWS", "sender": "NWS"} if official else {"type": "NEWS"}
    message = Message("OSINT", text, received_at=when, fields=fields)
    message_id = store.save_message(message, text + when.isoformat())
    result = AnalysisResult(True, "Infrastructure", urgency, text, 0.9, ())
    store.save_analysis(message_id, result)
    report = store.assign_report(message_id, message, result, state="SC", region=4)
    event_id = store.assign_event(report.report_id, "Infrastructure", {"water"}, Place(34.88, -82.7, "city", "SC", 4),
                                  text, "", key_terms(text))
    return message, result, report.report_id, event_id


def test_any_event_is_found_by_its_id(tmp_path) -> None:
    from s2_watch.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "s2.db")
    _message, _result, _report, event_id = add_event(store)
    assert store.event_by_id(event_id).title.startswith("Water main break")
    assert store.event_by_id(event_id + 99) is None


def test_clicking_an_alert_opens_its_event_even_when_the_list_hides_it(main_window) -> None:
    window = main_window()
    _message, result, report_id, event_id = add_event(window.store)
    window.show_alerts([(result, report_id)])
    assert window._alert_target == ("event", event_id)
    window.alert_clicked()
    assert window.tabs.currentWidget() is window.events_view
    detail = window.events_view.detail
    assert detail.current.event_id == event_id                      # 10 days old: outside the 48 h period
    assert "hidden by the current filters" in detail.browser.toPlainText()
    window.events_view.refresh()                                     # stays through a refresh
    assert detail.current.event_id == event_id


def test_several_alerts_open_the_review_list(main_window) -> None:
    window = main_window()
    first = add_event(window.store, "Water main break in Pickens")
    second = add_event(window.store, "Gas leak in Oldsmar")
    window.show_alerts([(first[1], first[2]), (second[1], second[2])])
    assert window._alert_target == ("review", None)


def test_a_single_click_on_the_tray_icon_restores_the_window(main_window, monkeypatch) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    window = main_window()
    shown = []
    monkeypatch.setattr(window, "show_from_tray", lambda: shown.append(True))
    window.tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert shown


def test_a_marker_click_opens_its_event_under_the_map(main_window) -> None:
    window = main_window()
    message, _result, report_id, event_id = add_event(window.store)
    window.remember_marker_report(message, report_id)
    window.select_analysis_message(str(id(message)))
    assert window.news_mode == "events"
    assert window.map_event_detail.current.event_id == event_id
    assert "Not in the list under the map" in window.map_event_detail.browser.toPlainText()


def test_a_station_marker_says_it_has_no_report(main_window) -> None:
    window = main_window()
    window.select_analysis_message("12345")
    assert "station or routine traffic" in window.notices[0][1]


def test_the_memory_check_is_only_for_ollama_on_this_computer() -> None:
    assert local_endpoint("http://127.0.0.1:11434") and local_endpoint("http://localhost:11434")
    assert not local_endpoint("http://192.168.1.50:11434") and not local_endpoint("http://ai-box.local:11434")
