import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.analysis.base import AnalysisResult
from s2_watch.dedup import report_text, same_report
from s2_watch.models import Message
from s2_watch.monitoring import message_key
from s2_watch.storage import SQLiteStore

T0 = datetime(2026, 9, 15, 23, 48, tzinfo=timezone.utc)
NEWS = AnalysisResult(True, "Climate / Environment", "medium", "", 0.7)


def test_envelope_is_removed_from_feeder_copies_and_relays() -> None:
    copies = [
        "MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL",
        "W1NEM: @MAGNET  ,EM29RL,3,Y48,211111111111,MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL,{&%} ♢ ",
        "KR1FLE: N1MAG> K7RIE MSG MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL *DE* KR1FLE ♢ ",
        "N7CWR: KG5RKW MSG MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL FROM W1NEM ♢",
    ]
    assert {report_text(text) for text in copies} == {"MO FLOOD WATCH DUE TO EXCESIVE RAINFALL"}
    assert not same_report(report_text("TX HEAT ADVISORY"), report_text("TN HEAT ADVISORY"))


def save(store, application, text, when, **fields):
    message = Message(application, text, received_at=when, fields=fields)
    message_id = store.save_message(message, message_key(message))
    return store.assign_report(message_id, message, NEWS, state="MO", region=7)


def test_copies_within_a_day_are_one_report(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    first = save(store, "MagCommstat", "MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL", T0, type="STATREP")
    second = save(store, "JS8SuperSpotter", "W1NEM: @MAGNET  ,EM29RL,3,Y48,211111111111,MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL,{&%} ♢ ",
                  T0 + timedelta(minutes=3), type="ACTIVITY")
    later = save(store, "MagCommstat", "MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL", T0 + timedelta(hours=30), type="STATREP")
    assert first.new_report and not second.new_report
    assert second.report_id == first.report_id
    assert second.copies == 2 and second.sources == ("MagCommstat", "JS8SuperSpotter")
    assert later.new_report and later.report_id != first.report_id


def test_reading_a_message_again_does_not_count_another_copy(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    first = save(store, "MagCommstat", "MO - FLOOD WATCH", T0, type="STATREP")
    again = save(store, "MagCommstat", "MO - FLOOD WATCH", T0, type="STATREP")
    assert again.report_id == first.report_id and not again.new_report and again.copies == 1
    assert len(store.search_history(text="FLOOD")) == 1


def test_repeated_messages_from_earlier_versions_are_removed_once(tmp_path: Path) -> None:
    path = tmp_path / "s2.db"
    SQLiteStore(path).close()
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM settings WHERE key = 'migrations.repeated_messages_removed'")
    for text in ("HAIL", "HAIL", "HAIL", "WIND"):
        connection.execute(
            "INSERT INTO messages(source_application, form_name, raw_content, received_at) VALUES ('JS8Call', 'W', ?, '2026-09-01')",
            (text,),
        )
    connection.commit()
    connection.close()
    store = SQLiteStore(path)
    assert sorted(row[3] for row in store.search_history()) == ["HAIL", "WIND"]
