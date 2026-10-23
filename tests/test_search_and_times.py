"""Searching the lists, the 400-row limit, and Local/Zulu reaching the events lists."""

from datetime import timedelta

from s2_watch.storage import SQLiteStore
from s2_watch.ui.events_view import EventsTable

from tests.test_events import T0, TULSA, add


def test_search_finds_events_by_their_reports_text_and_ignores_the_row_limit(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    flood = add(store, "MagCommstat", "TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA, fields={"sender": "KD0ABC"})
    fire = add(store, "MagCommstat", "TULSA OK - GRASS FIRE NEAR HIGHWAY 75", T0 + timedelta(hours=1), TULSA)
    table = EventsTable(str, store=store)
    table.set_events(store.events_since(T0 - timedelta(hours=1)), limit=1)
    visible = lambda: {table.item(r, 0).data(table.EVENT_ROLE) for r in range(table.rowCount()) if not table.isRowHidden(r)}
    assert len(visible()) == 1                                   # the row limit
    assert table.set_search("kd0abc roads") == 1 and visible() == {flood}   # a call sign and a word from the text
    assert table.set_search("highway") == 1 and visible() == {fire}
    assert table.set_search("") == 2 and len(visible()) == 1     # cleared: the usual list again


def test_the_list_under_the_map_can_show_400_rows_and_zulu_reaches_the_events(tmp_path) -> None:
    from s2_watch.ui.main_window import MainWindow

    store = SQLiteStore(tmp_path / "s2.db")
    add(store, "MagCommstat", "TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA)
    store.close()
    window = MainWindow(str(tmp_path / "s2.db"))
    try:
        assert window.news_rows.maximum() == 400
        window.review_duration.setCurrentIndex(window.review_duration.count() - 1)   # long enough to include T0
        window.events_view.period.setCurrentIndex(window.events_view.period.count() - 1)
        window.time_zone.setCurrentIndex(window.time_zone.findData("utc"))
        window.events_view.refresh()
        text = window.events_view.table.item(0, 0).text()
        window.time_zone.setCurrentIndex(window.time_zone.findData("local"))
        assert window.events_view.table.item(0, 0).text() != text or text.endswith("L")
        assert text.endswith("Z") and window.events_view.table.item(0, 0).text().endswith("L")
    finally:
        window.close()


def test_history_finds_a_station_by_its_call_sign_and_every_word(tmp_path) -> None:
    from s2_watch.models import Message
    from s2_watch.monitoring import message_key

    store = SQLiteStore(tmp_path / "s2.db")
    for text, sender in (("QST YEMEN LL45 SAUDI-LED COALITION AIRSTRIKES", "K9DGY"),
                         ("QST RETAIL CYBER UPDATE", "K9DGY"), ("YEMEN NET CHECK-IN", "W1AW")):
        message = Message("MagCommstat", text, fields={"sender": sender})
        store.save_message(message, message_key(message))
    assert [row[6] for row in store.search_history(text="k9dgy")] == ["K9DGY", "K9DGY"]   # the sender, not the text
    assert [row[3] for row in store.search_history(text="K9DGY yemen")] == ["QST YEMEN LL45 SAUDI-LED COALITION AIRSTRIKES"]
    assert len(store.search_history(text="yemen")) == 2
    assert store.search_history(text="K9DGJ") == []
