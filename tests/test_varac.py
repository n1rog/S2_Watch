"""VarAC: broadcasts, Inbox VarAC mail and incoming chat, read from VarAC.db."""

import sqlite3
from datetime import timedelta
from pathlib import Path

from s2_watch.adapters.varac_sqlite import VarACSQLiteAdapter, is_protocol
from s2_watch.exercise import exercise_kind

SCHEMA = """
CREATE TABLE broadcast (id INTEGER PRIMARY KEY, guid TEXT, broadcast_time TEXT, frequency INTEGER, from_callsign TEXT,
    to_callsign TEXT, via_callsign TEXT, broadcast_message TEXT, snr INTEGER, band TEXT, instance_id INTEGER);
CREATE TABLE vmail (id INTEGER PRIMARY KEY, guid TEXT, creation_time TEXT, sent_time TEXT, received_time TEXT,
    folder_id INTEGER, vmail_to TEXT, vmail_from TEXT, vmail_via TEXT, delivery_band TEXT, delivery_snr INTEGER,
    subject TEXT, msg TEXT, read_status INTEGER, is_deleted INTEGER, frequency INTEGER, has_attachment INTEGER,
    urgent INTEGER);
CREATE TABLE datastream (id INTEGER PRIMARY KEY, guid TEXT, datastream_entry_type_id INTEGER, qso_guid TEXT,
    callsign TEXT, entry TEXT, file_path TEXT, chat_id TEXT, reply_on_chat_id TEXT, creation_time TEXT,
    is_deleted INTEGER);
CREATE TABLE cqframe (id INTEGER PRIMARY KEY, from_callsign TEXT, locator TEXT);
CREATE TABLE contact (id INTEGER PRIMARY KEY, callsign TEXT, locator TEXT);
"""


def varac_db(tmp_path: Path) -> Path:
    path = tmp_path / "VarAC.db"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO broadcast(broadcast_time, frequency, from_callsign, to_callsign, via_callsign,"
                       " broadcast_message, snr, band) VALUES (?, 7115000, ?, ?, '', ?, -5, '40m')", [
                           ("2099-11-13 04:02:35.2758345Z", "W1KEB", "ALL",
                            "EXERCISE EXERCISE EXERCISE THIS IS A TEST OF THE MAGNET S2 SYSTEM"),
                           ("2099-11-14 23:01:35.9650005Z", "KG6MTM", "EMCOMM", "<EC>"),           # the EmComm beacon
                           ("2099-11-11 23:31:37.1791865Z", "W5TWM", "ALL", "«QF:3585000»"),       # a QSY frame
                           ("2099-11-14 20:00:00.0Z", "KG5RKW", "ALL", "BRIDGE OUT ON US-281 AT MARBLE FALLS"),
                       ])
        db.execute("INSERT INTO vmail(creation_time, received_time, folder_id, vmail_to, vmail_from, subject, msg,"
                   " urgent, is_deleted) VALUES ('2099-11-14 19:00:00Z', '2099-11-14 19:00:00Z', 1, 'N1ROG', 'KB6CIO',"
                   " 'Water main break', 'Boil order for Ada OK', 1, 0)")
        db.execute("INSERT INTO vmail(creation_time, folder_id, vmail_to, vmail_from, subject, msg, is_deleted)"
                   " VALUES ('2099-11-14 19:00:00Z', 2, 'N1MAG', 'N1ROG', 'Sent mail', 'not read', 0)")   # Sent
        db.executemany("INSERT INTO datastream(datastream_entry_type_id, callsign, entry, creation_time, is_deleted)"
                       " VALUES (?, ?, ?, '2099-11-14 18:00:00Z', 0)", [
                           (1, "K7RIE", "Power is out across Pendleton, OR since 1500Z"),
                           (1, "K7RIE", "de K7RIE <R-08> <S:VB>"),       # protocol only
                           (1, "K7RIE", "<SFRD>"),
                           (2, "N1ROG", "my own line"),                  # outgoing
                           (3, "", "NO LUCK. Maybe next time..."),       # VarAC's info line
                       ])
        db.execute("INSERT INTO cqframe(from_callsign, locator) VALUES ('KG5RKW', 'EL06VD')")
        db.execute("INSERT INTO contact(callsign, locator) VALUES ('K7RIE', 'CN96OU')")
    return path


def test_varac_broadcasts_mail_and_chat_are_read(tmp_path: Path) -> None:
    adapter = VarACSQLiteAdapter(varac_db(tmp_path), timedelta(days=30))
    messages = adapter.read_messages()
    assert adapter.status == "Connected"
    texts = {message.raw_content: message for message in messages}
    assert set(texts) == {"EXERCISE EXERCISE EXERCISE THIS IS A TEST OF THE MAGNET S2 SYSTEM",
                          "BRIDGE OUT ON US-281 AT MARBLE FALLS", "Water main break\nBoil order for Ada OK",
                          "Power is out across Pendleton, OR since 1500Z"}
    bridge = texts["BRIDGE OUT ON US-281 AT MARBLE FALLS"]
    assert bridge.source_application == "VarAC" and bridge.fields["type"] == "BROADCAST"
    assert bridge.fields["grid"] == "EL06VD" and bridge.fields["frequency"] == "7115000"
    mail = texts["Water main break\nBoil order for Ada OK"]
    assert mail.fields["sender"] == "KB6CIO" and mail.fields["priority"] == "urgent"
    assert texts["Power is out across Pendleton, OR since 1500Z"].fields["grid"] == "CN96OU"
    assert exercise_kind(texts["EXERCISE EXERCISE EXERCISE THIS IS A TEST OF THE MAGNET S2 SYSTEM"]) == "exercise"
    assert adapter.read_messages() == []                 # only new rows next time


def test_protocol_frames_are_not_messages() -> None:
    assert is_protocol("<SFRD>") and is_protocol("de K7RIE <R-08> <S:VB>") and is_protocol("<AWAY> de N1MAG <R+04> <S:B>")
    assert not is_protocol("msg received - HES") and not is_protocol("<SFOK> Power out in Pendleton")


def test_a_missing_varac_database_is_reported(tmp_path: Path) -> None:
    adapter = VarACSQLiteAdapter(tmp_path / "nope" / "VarAC.db")
    assert adapter.read_messages() == [] and adapter.status == "Database not found"


def test_varac_is_a_source(main_window) -> None:
    window = main_window()
    assert "VarAC" in window.map_application_filters
    assert window.varac_adapter is not None and window.varac_adapter.application_name == "VarAC"
    assert any(form.application == "VarAC" for form in window.store.list_watch_forms())
