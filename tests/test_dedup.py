import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.analysis.base import AnalysisResult
from s2_watch.dedup import report_text, same_report
from s2_watch.models import Message
from s2_watch.monitoring import legacy_message_key, message_key
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


def test_revised_osint_event_stays_one_message_and_report(tmp_path: Path) -> None:
    # USGS refines a quake's location minutes later; same event id, new text.
    store = SQLiteStore(tmp_path / "s2.db")
    first = Message("OSINT", "USGS: M4.2 earthquake — 7 km ESE of Denali National Park, Alaska (63.507, -151.604)",
                    received_at=T0, fields={"type": "EARTHQUAKE", "message_id": "ak0261"})
    revised = Message("OSINT", "USGS: M4.2 earthquake — 7 km SE of Denali National Park, Alaska (63.492, -151.620)",
                      received_at=T0, fields={"type": "EARTHQUAKE", "message_id": "ak0261"})
    first_id = store.save_message(first, message_key(first))
    report = store.assign_report(first_id, first, NEWS)
    revised_id = store.save_message(revised, message_key(revised))
    assert revised_id == first_id
    assert store.assign_report(revised_id, revised, NEWS).report_id == report.report_id


def test_message_saved_under_the_old_key_is_found_and_rekeyed(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    quake = Message("OSINT", "USGS: M4.1 earthquake — 21 km W of Preston, Nevada (38.932, -115.308)",
                    received_at=T0, fields={"type": "EARTHQUAKE", "message_id": "nn0001"})
    old_id = store.save_message(quake, legacy_message_key(quake))   # saved by 0.10.0
    assert store.save_message(quake, message_key(quake), legacy_message_key(quake)) == old_id
    assert store.save_message(quake, message_key(quake)) == old_id   # now under the new key
    count = sqlite3.connect(tmp_path / "s2.db").execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert count == 1


def test_recent_reports_rebuild_the_table_without_the_sources(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    statrep = Message("MagCommstat", "MO - FLOOD WATCH", received_at=T0, fields={"type": "STATREP", "grid": "EM29RL"})
    message_id = store.save_message(statrep, message_key(statrep))
    store.save_analysis(message_id, NEWS)
    report = store.assign_report(message_id, statrep, NEWS, state="MO", region=7)
    store.set_report_status([report.report_id], "interest", "watch the river")
    old = Message("MagCommstat", "TX HEAT ADVISORY", received_at=T0 - timedelta(days=3))
    old_id = store.save_message(old, message_key(old))
    store.save_analysis(old_id, NEWS)
    store.assign_report(old_id, old, NEWS)

    [(report_id, message, result)] = store.recent_reports(T0 - timedelta(hours=24))
    assert report_id == report.report_id
    assert message == statrep.__class__(**{**statrep.__dict__, "form_name": None})
    assert message.fields["grid"] == "EM29RL"          # kept for the map
    assert result == NEWS
    info = store.report_info(report_id)
    assert (info.status, info.note, info.region) == ("interest", "watch the river", 7)


def test_fields_are_added_to_messages_saved_before_they_were_kept(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    bare = Message("MagCommstat", "MO - FLOOD WATCH", received_at=T0)
    message_id = store.save_message(bare, message_key(bare))
    store.assign_report(message_id, bare, NEWS)
    store.save_analysis(message_id, NEWS)
    store.save_message(Message("MagCommstat", "MO - FLOOD WATCH", received_at=T0, fields={"grid": "EM29RL"}), message_key(bare))
    assert store.recent_reports(T0 - timedelta(hours=1))[0][1].fields == {"grid": "EM29RL"}
