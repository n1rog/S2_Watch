import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from s2_watch.analysis.base import AnalysisResult
from s2_watch.models import Message
from s2_watch.monitoring import message_key
from s2_watch.storage import SQLiteStore


def add(store, source, text, category, minutes_ago, state="", region=None):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    message = Message(source, text, received_at=when, fields={"sender": "W1AW"})
    message_id = store.save_message(message, message_key(message))
    result = AnalysisResult(True, category, "medium", text, 0.7)
    store.save_analysis(message_id, result)
    return store.assign_report(message_id, message, result, state, region)


def test_category_share_counts_reports_not_copies(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from s2_watch.ui.metrics_view import MetricsView

    QApplication.instance() or QApplication([])
    store = SQLiteStore(tmp_path / "s2.db")
    add(store, "MagCommstat", "PORT CLOSURE IN LONG BEACH", "Supply Chain", 30, "CA", 9)
    add(store, "JS8SuperSpotter", "K6NLX: @MAGNET MSG PORT CLOSURE IN LONG BEACH ♢", "Supply Chain", 28, "CA", 9)
    for index in range(3):
        add(store, "MagCommstat", f"FLASH FLOOD WARNING NUMBER {index}", "Climate / Environment", 60 + index, "AZ", 9)

    view = MetricsView(store)
    view.period.setCurrentIndex(0)  # last 24 hours
    view.refresh()
    rows = {label: text for label, _values, text, _tip in view.categories.rows}
    assert rows["Supply Chain"] == "25% · 1"          # 1 of 4 reports, though 2 copies arrived
    assert rows["Climate / Environment"] == "75% · 3"
    assert view.duplicates_tile.value.text() == "1"
    assert view.reports_tile.value.text() == "4"
    sources = {label: values for label, values, _text, _tip in view.sources.rows}
    assert sources["MagCommstat"] == (4, 0) and sources["JS8SuperSpotter"] == (0, 1)


def test_labels_persist_and_ignored_reports_leave_the_metrics(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from s2_watch.ui.metrics_view import MetricsView

    QApplication.instance() or QApplication([])
    store = SQLiteStore(tmp_path / "s2.db")
    shortage = add(store, "MagCommstat", "EGG SHORTAGE AT STORES", "Supply Chain", 30)
    flood = add(store, "MagCommstat", "FLASH FLOOD WARNING", "Climate / Environment", 40)
    store.set_report_status([shortage.report_id], "followup", "S2-0926-01")
    store.set_report_status([flood.report_id], "ignored")
    assert store.report_status(shortage.report_id) == ("followup", "S2-0926-01")
    # A later copy of a labelled report carries the label (so an ignored report stays quiet).
    again = add(store, "JS8SuperSpotter", "W1AW: @MAGNET MSG FLASH FLOOD WARNING ♢", "Climate / Environment", 35)
    assert again.report_id == flood.report_id and again.status == "ignored"
    # Changing the status keeps the note unless a new one is given; clearing removes it.
    store.set_report_status([shortage.report_id], "interest")
    assert store.report_status(shortage.report_id) == ("interest", "S2-0926-01")
    store.set_report_status([shortage.report_id], "", "")
    assert store.report_status(shortage.report_id) == ("", "")

    view = MetricsView(store)
    view.period.setCurrentIndex(0)
    view.refresh()
    assert view.reports_tile.value.text() == "1"
    assert "1 ignored, not counted" in view.reports_tile.detail.text()
    assert {label for label, values, _t, _tip in view.categories.rows if values[0]} == {"Supply Chain"}


def test_metrics_scope_limits_to_focus_regions(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from s2_watch.ui.metrics_view import MetricsView

    QApplication.instance() or QApplication([])
    store = SQLiteStore(tmp_path / "s2.db")
    add(store, "MagCommstat", "FLASH FLOOD IN DENVER", "Climate / Environment", 30, "CO", 8)
    add(store, "MagCommstat", "PORT CLOSURE IN HOUSTON", "Supply Chain", 30, "TX", 6)
    add(store, "MagCommstat", "BOMB THREAT AT SCHOOL", "Terrorism", 30, "", None)
    view = MetricsView(store)
    view.period.setCurrentIndex(0)
    view.set_focus_regions({8})
    view.scope.setCurrentIndex(0)   # my focus regions
    view.refresh()
    assert view.reports_tile.value.text() == "1"
    assert {label for label, values, _t, _tip in view.categories.rows if values[0]} == {"Climate / Environment"}
    view.scope.setCurrentIndex(1)   # all of the U.S.
    view.refresh()
    assert view.reports_tile.value.text() == "3"
    view.set_focus_regions(set())   # no focus: scope hidden, everything counts
    view.refresh()
    assert view.scope.isHidden() and view.reports_tile.value.text() == "3"
