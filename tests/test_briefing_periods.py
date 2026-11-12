"""One way to draft a briefing, its period, and briefings drafted at shift change (review P5, P6)."""

from datetime import datetime, timedelta, timezone

from s2_watch import report_files
from s2_watch.briefing_periods import (BRIEFING_TIMES_SETTING, LAST_SCHEDULED_SETTING, briefing_times, due_briefing,
                                       hours_between, period_start, shift_start)
from s2_watch.storage import SQLiteStore

NOW = datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc)


def test_schedule_times_are_read_as_utc_hhmm() -> None:
    assert briefing_times("1745, 05:45 1745 nonsense 2599") == ["0545", "1745"]
    assert briefing_times("") == []


def test_periods_start_where_the_operator_expects(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    assert period_start("since_last", NOW, store) == NOW - timedelta(hours=24)          # no briefing yet
    store.set_setting("briefing.last", (NOW - timedelta(hours=14)).isoformat())
    assert period_start("since_last", NOW, store) == NOW - timedelta(hours=14)
    assert period_start("shift", NOW, store) == NOW.replace(hour=18, minute=0)          # 0600/1800 shifts
    store.set_setting(BRIEFING_TIMES_SETTING, "0545 1745")
    assert period_start("shift", NOW, store) == NOW.replace(hour=17, minute=45)
    assert shift_start(NOW.replace(hour=3), ["0545", "1745"]) == (NOW - timedelta(days=1)).replace(hour=17, minute=45)
    assert period_start("12", NOW, store) == NOW - timedelta(hours=12)
    assert period_start("custom", NOW, store, 5) == NOW - timedelta(hours=5)
    assert hours_between(NOW - timedelta(hours=13, minutes=15), NOW) == 14


def test_a_scheduled_briefing_is_due_once_and_not_when_long_missed(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    assert due_briefing(NOW, store) is None                                             # no schedule
    store.set_setting(BRIEFING_TIMES_SETTING, "1845")
    assert due_briefing(NOW, store) == NOW.replace(minute=45, hour=18)
    store.set_setting(LAST_SCHEDULED_SETTING, NOW.replace(minute=45, hour=18).isoformat())
    assert due_briefing(NOW, store) is None                                             # drafted already
    store.set_setting(BRIEFING_TIMES_SETTING, "1500")
    assert due_briefing(NOW, store) is None                                             # 4 h late: S2 Watch was off


def test_reports_go_to_a_folder_per_day_written_whole(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    assert report_files.reports_folder(store).name == "S2 Watch Reports"
    store.set_setting(report_files.FOLDER_SETTING, str(tmp_path / "out"))
    assert report_files.day_folder(store, NOW) == tmp_path / "out" / "2026-11-12"
    paths = report_files.save_all(tmp_path / "out" / "day", "MAGNET_S2_TEST", "SUBJECT: Test", "TEST", "TEST", False)
    assert [path.name for path in paths] == ["MAGNET_S2_TEST.txt", "MAGNET_S2_TEST_FLMSG.txt", "MAGNET_S2_TEST_JS8Call.txt"]
    assert not list((tmp_path / "out" / "day").glob("*.part*"))


def test_every_draft_briefing_button_asks_the_same_period(main_window, monkeypatch) -> None:
    from s2_watch.ui import main_window as module

    window = main_window()
    asked = []

    class Chooser:
        def __init__(self, *args):
            asked.append(args)

        def exec(self):
            return 0          # cancelled

    monkeypatch.setattr(module, "BriefingPeriodDialog", Chooser)
    window.draft_briefing()
    window.events_view.draft_briefing.emit()
    assert len(asked) == 2


def test_the_period_dialog_lists_the_events_to_include(main_window) -> None:
    from PySide6.QtCore import Qt

    from s2_watch.ui.briefing_dialog import BriefingPeriodDialog
    from tests.test_navigation import add_event

    window = main_window()
    add_event(window.store, "Water main break in Pickens", datetime.now(timezone.utc) - timedelta(hours=2))
    dialog = BriefingPeriodDialog(window.store, window.briefing_events)
    assert dialog.key() == "since_last" and dialog.list.count() == 1
    assert "<b>1</b> of 1 events included" in dialog.summary.text()
    dialog.list.item(0).setCheckState(Qt.CheckState.Unchecked)
    dialog.accept()
    assert dialog.events == []


def test_a_briefing_is_drafted_at_shift_change_for_review(main_window) -> None:
    window = main_window()
    now = datetime.now(timezone.utc)
    window.store.set_setting(BRIEFING_TIMES_SETTING, now.strftime("%H%M"))
    window.check_scheduled_briefing()
    window.check_scheduled_briefing()                        # once per scheduled time
    drafts = window.store.list_drafts()
    assert [(draft["kind"], draft["origin"]) for draft in drafts] == [("briefing", "scheduled")]
    assert "drafted" in window.notices[0][1]
