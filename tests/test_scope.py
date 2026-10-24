"""Area of responsibility, radio silence near events, and the operator's intelligence entries."""

from datetime import timedelta
from pathlib import Path

from s2_watch import focus
from s2_watch.adapters.analyst import AnalystAdapter, entry_message
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.events import EventSummary
from s2_watch.focus import FocusArea
from s2_watch.models import WatchForm
from s2_watch.monitoring import Monitor, message_key
from s2_watch.silence import quiet_cells, quiet_near
from s2_watch.storage import SQLiteStore

from tests.test_events import T0, TULSA, add

TULSA_AREA = FocusArea("Tulsa, OK", 36.12, -95.93, 100)


def event(points, region=6, closed=None):
    return EventSummary(1, "Flooding", "Climate / Environment", "OK", region, T0, T0, 1, ("MagCommstat",), "high",
                        "", None, points=points, closed_at=closed)


def test_an_area_of_responsibility_adds_to_the_focus_regions() -> None:
    try:
        focus.set_area(TULSA_AREA)
        assert focus.event_in_focus(event([(36.0, -95.9)], region=6), set())          # inside the circle
        assert not focus.event_in_focus(event([(35.47, -97.52)], region=6), set())   # Oklahoma City: 160 km
        assert focus.event_in_focus(event([(35.47, -97.52)], region=6), {6})         # but in a chosen region
        assert focus.describe({6}) == "MARS Region 6 and 100 km around Tulsa, OK"
        assert FocusArea.from_json(TULSA_AREA.to_json()) == TULSA_AREA
    finally:
        focus.set_area(None)
    assert not focus.has_focus(set())


def cell(grid, south, west, first, last, messages, stations=("K1ABC",)):
    return {"grid": grid, "south": south, "west": west, "first": first.isoformat(), "last": last.isoformat(),
            "messages": messages, "stations": list(stations)}


def test_stations_that_go_quiet_near_an_open_event_are_reported() -> None:
    now = T0 + timedelta(hours=12)
    busy_then_quiet = cell("EM26", 36, -96, T0, now - timedelta(hours=5), 40)     # ~5/h, silent 5 h
    always_quiet = cell("EM16", 36, -98, T0, now - timedelta(hours=5), 3)          # too few to judge
    chatty = cell("EM36", 36, -94, T0, now - timedelta(minutes=10), 40)            # still talking
    cells = [busy_then_quiet, always_quiet, chatty]
    assert [c["grid"] for c in quiet_cells(cells, now)] == ["EM26"]
    [near] = quiet_near(event([(36.15, -95.99)]), cells, now)
    assert near["grid"] == "EM26" and near["silent_hours"] == 5 and near["usual_per_3h"] >= 4
    assert quiet_near(event([(30.0, -90.0)]), cells, now) == []                    # far away
    assert quiet_near(event([(36.15, -95.99)], closed=now), cells, now) == []      # over already


def test_an_official_entry_becomes_news_and_confirms_its_event(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    radio = add(store, "MagCommstat", "TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA)
    entry_id = store.add_analyst_entry(T0 + timedelta(hours=2), "Oklahoma Fusion Center", "TLP:CLEAR", True,
                                       "Climate / Environment", "high", "Tulsa, OK",
                                       "Flooding confirmed along the Arkansas River in Tulsa, OK")
    saved = []
    monitor = Monitor([AnalystAdapter(store.analyst_entries())], [WatchForm("JS8Call", "unrelated", "nothing")],
                      store, lambda _m: None, RuleBasedNewsAnalyzer(),
                      on_saved=lambda message_id, message, result: saved.append(
                          (store.assign_report(message_id, message, result), result)))
    monitor.poll()                                   # kept although no watch form matches it
    report, result = saved[0]
    assert result.newsworthy and result.category == "Climate / Environment" and result.urgency == "high"
    store.assign_event(report.report_id, "Climate / Environment", {"flood"}, TULSA, result.summary)
    [flood] = [e for e in store.events_since(T0 - timedelta(hours=1)) if e.event_id == radio]
    assert flood.official == "Oklahoma Fusion Center" and flood.reports == 2
    [entry] = store.analyst_entries()
    store.remove_analyst_entry(entry_id, message_key(entry_message(entry)))
    assert store.analyst_entries() == []
    [flood] = [e for e in store.events_since(T0 - timedelta(hours=1)) if e.event_id == radio]
    assert flood.reports == 1 and not flood.official


def test_the_map_reloads_itself_when_its_engine_is_killed(monkeypatch) -> None:
    from PySide6.QtWebEngineCore import QWebEnginePage

    from s2_watch.ui import map_view as module

    view = module.MapView()
    reloads, messages = [], []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda ms, callback: reloads.append(ms))
    view.engine_restarted.connect(messages.append)
    killed = QWebEnginePage.RenderProcessTerminationStatus.KilledTerminationStatus
    view._engine_ended(killed, 9)
    view._engine_ended(killed, 9)                         # again soon after: wait longer
    view._engine_ended(QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus, 0)   # closing: nothing
    assert reloads == [2000, 10000] and not view._page_ready
    view.deleteLater()                       # its web page must be gone before the run ends
    from tests.conftest import destroy_pending

    destroy_pending()
    assert messages[0] == "The map was stopped (the computer may have run out of memory); reloading it in 2 s."
