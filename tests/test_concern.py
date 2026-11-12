"""An event's concern level, explainable from its reasons; menus, notices and the event panel (review P7, P8)."""

from datetime import datetime, timedelta, timezone

from s2_watch.concern import concern
from s2_watch.events import EventSummary
from s2_watch.s2_report import estimative

NOW = datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc)


def event(urgency="high", official="", sources=1, region=6, hours_ago=1, radio=True, closed=False):
    seen = NOW - timedelta(hours=hours_ago)
    return EventSummary(1, "Flooding", "Climate / Environment", "CO", region, seen, seen, sources,
                        ("JS8Call",) if radio else ("OSINT",), urgency, official, None,
                        closed_at=seen if closed else None, independent_sources=sources)


def test_levels_follow_the_rules_and_say_why() -> None:
    high = concern(event(official="NWS Flood Warning"), NOW, {6})
    assert high.level == "High"
    assert high.text() == ("High — high urgency · confirmed by NWS Flood Warning · 1 source · in focus region 6")
    assert concern(event(sources=3), NOW, {6}).level == "High"                     # 3 stations
    assert concern(event(), NOW, {6}).level == "Elevated"                          # high, unconfirmed
    assert concern(event(official="NWS"), NOW, {8}).level == "Elevated"            # outside the focus regions
    assert "outside your focus regions" in concern(event(official="NWS"), NOW, {8}).reasons
    assert concern(event(urgency="medium", sources=2), NOW, {6}, "escalating").level == "Elevated"
    assert concern(event(urgency="medium"), NOW, {6}).level == "Guarded"
    single = concern(event(urgency="low", hours_ago=3), NOW, {6})
    assert single.level == "Guarded" and "single source, not yet confirmed" in single.reasons
    assert concern(event(urgency="low", radio=False), NOW, {6}).level == "Low"
    news = concern(event(sources=4, radio=False), NOW, {6})              # 4 outlets: wide reporting, not confirmation
    assert news.level == "Elevated" and "news only" in news.reasons
    assert concern(event(radio=False), NOW, {6}).level == "Guarded"      # one outlet's story
    assert concern(event(official="NWS", hours_ago=20), NOW, {6}).level == "Elevated"   # quiet: a level down
    assert concern(event(official="NWS", closed=True), NOW, {6}).level == "Low"
    assert concern(event(official="NWS"), NOW, set()).level == "High"              # no focus regions: all count


def test_probability_levels_show_the_standard_ranges() -> None:
    assert estimative("HIGH") == "Very likely (80–95%)"
    assert estimative("moderate") == "Likely (55–80%)" and estimative("LOW") == "Unlikely (20–45%)"
    assert estimative("") == ""


def test_the_event_lists_and_panel_show_the_concern(main_window) -> None:
    from tests.test_navigation import add_event

    window = main_window()
    _m, _r, _report, event_id = add_event(window.store, when=datetime.now(timezone.utc) - timedelta(hours=1))
    window.events_view.refresh()
    table = window.events_view.table
    column = table.COLUMNS.index("Concern")
    assert table.item(0, column).text() == "Guarded"                   # high urgency, but one news story
    assert table.item(0, column).toolTip() == "Guarded — high urgency · 1 source, unconfirmed"
    window.open_event(event_id)
    assert "Concern: Guarded — high urgency · 1 source, unconfirmed" in window.events_view.detail.browser.toPlainText()


def test_tools_are_in_their_own_menu(main_window) -> None:
    window = main_window()
    menus = {action.text(): [item.text() for item in action.menu().actions() if item.text()]
             for action in window.menuBar().actions() if action.menu()}
    assert menus["Reports"] == ["New Briefing…", "AI Assessment of Selected Event", "Add Intelligence Entry…",
                                "Intelligence Entries…", "Reports Library"]
    assert menus["Tools"] == ["Sender Track Records…", "Keyword Noise…", "Metrics"]


def test_notices_are_kept_and_counted_until_read(main_window) -> None:
    window = main_window()
    window.notify("Database backed up")
    window.notify("AI assessment ready", event_id=7)
    assert window.notices_button.text() == "🔔 2"
    window.fill_notices_menu()                          # opening the bell
    texts = [action.text() for action in window.notices_menu.actions()]
    assert texts[0].endswith("AI assessment ready") and texts[1].endswith("Database backed up")
    assert window.notices_button.text() == "🔔"
    assert window.review_view.notices.count() == 2      # also in Needs Review


def test_the_event_panel_buttons_come_in_one_order(main_window) -> None:
    window = main_window()
    for detail in (window.map_event_detail, window.events_view.detail):
        layout = detail.layout().itemAt(1).layout()
        labels = [layout.itemAt(index).widget().text() for index in range(layout.count())
                  if layout.itemAt(index).widget() is not None]
        assert labels[:4] == ["AI assessment", "Draft follow-up…", "Show on map", "Close event…"]
