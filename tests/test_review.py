"""Needs Review lists what waits on the operator; the Reports tab holds every draft (review P3, P4)."""

from datetime import datetime, timedelta, timezone

from s2_watch.event_assessment import EventAssessment
from tests.test_navigation import add_event

NOW = datetime.now(timezone.utc)


def test_needs_review_is_the_first_tab_and_counts_what_waits(main_window) -> None:
    window = main_window()
    assert window.tabs.widget(0) is window.review_view and window.tabs.currentWidget() is window.review_view
    _message, _result, _report, event_id = add_event(window.store, "Flash flood in Pickens", NOW - timedelta(hours=1),
                                                     official=True)       # High concern: officially confirmed
    add_event(window.store, "Shooting in Oldsmar", NOW - timedelta(hours=1))   # one news story: not "new"
    window.review_view.refresh()
    [item] = window.review_view.items
    assert (item.kind, item.target) == ("new", event_id)
    assert window.tabs.tabText(0) == "Needs Review (1)"
    window.review_act(item, "primary")                   # Open event: shown, and off the list
    assert window.tabs.currentWidget() is window.events_view
    assert window.events_view.detail.current.event_id == event_id
    assert window.review_view.items == [] and window.tabs.tabText(0) == "Needs Review"


def test_assessments_briefings_and_unsent_drafts_wait_for_review(main_window) -> None:
    window = main_window()
    store = window.store
    _m, _r, _report, event_id = add_event(store, "Gas leak in Oldsmar", NOW - timedelta(hours=30), urgency="medium")
    store.save_event_assessment(event_id, EventAssessment(model="qwen2.5:7b", reports=1))
    scheduled = store.save_draft("briefing", "SUBJECT: Situation Briefing\nDTG: 261112-1745Z", origin="scheduled")
    mine = store.save_draft("report", "SUBJECT: Flooding\nDTG: 261112-1800Z")
    sent = store.save_draft("report", "SUBJECT: Old news\nDTG: 261111-1800Z")
    store.mark_draft_sent(sent)
    window.review_view.refresh()
    kinds = {(item.kind, item.target) for item in window.review_view.items}
    assert kinds == {("assessment", event_id), ("briefing", scheduled), ("draft", mine)}
    draft = next(item for item in window.review_view.items if item.kind == "draft")
    window.review_act(draft, "secondary")                # Hide
    assert ("draft", mine) not in {(item.kind, item.target) for item in window.review_view.items}
    window.review_view.chips["assessment"].setChecked(False)          # a filter chip
    assert {item.kind for item in window.review_view.shown()} == {"briefing"}


def test_a_finished_assessment_is_announced(main_window) -> None:
    window = main_window()
    _m, _r, _report, event_id = add_event(window.store)
    window.store.save_event_assessment(event_id, EventAssessment(model="m", reports=1))
    window.assessment_ready(event_id)
    assert window.notices[0][1].startswith("AI assessment ready: Water main break")
    assert window._alert_target == ("event", event_id)


def test_the_reports_menu_opens_the_reports_tab(main_window) -> None:
    window = main_window()
    draft_id = window.store.save_draft("report", "SUBJECT: Flooding\nDTG: 261112-1800Z", report_id=None)
    window.show_saved_drafts()
    assert window.tabs.currentWidget() is window.reports_view
    assert window.reports_view.table.rowCount() == 1 and window.reports_view.select_draft(draft_id)


def test_a_report_draft_knows_its_event(tmp_path) -> None:
    from s2_watch.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "s2.db")
    _m, _r, report_id, event_id = add_event(store)
    draft_id = store.save_draft("report", "SUBJECT: Water main\nDTG: 261112-1800Z", report_id=report_id)
    assert store.get_draft(draft_id)["event_id"] == event_id
