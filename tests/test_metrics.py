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
