"""VarAC (VARA HF chat): broadcasts, VarAC mail and incoming chat, read from VarAC.db.

VarAC keeps everything in one SQLite database, ``VarAC.db`` in its program folder
(``~/.wine/drive_c/VarAC/VarAC.db`` under Wine). It is opened read-only, never written.

- ``broadcast``: messages broadcast on a frequency to ALL, a group (EMCOMM) or a station.
  VarAC's automatic frames carry no message and are skipped: ``<EC>`` (the EmComm
  beacon) and ``«QF:7115000»`` (a frequency change).
- ``vmail``: VarAC mail in the Inbox, subject and text.
- ``datastream``: lines received in a QSO's chat (VarAC's own "Info" lines, and lines
  that are only protocol frames such as ``<SFRD>`` or ``de K7RIE <R-08>``, are skipped).

A sender's grid square comes from its latest CQ frame, or its entry in VarAC's contacts,
so the station can be placed on the map.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.models import Message
from s2_watch.time_utils import is_recent, parse_source_time

INBOX = 1                 # vmail_folder: Inbox
INCOMING = 1              # datastream_entry_type: Incoming
_AUTOMATIC = re.compile(r"^\s*(?:<EC>|«QF:\d+»)\s*$")
# VarAC's protocol frames in a chat: "<SFRD>", "<SFOK>", "de K7RIE <R-08> <S:VB>", "<AWAY> de N1MAG ..."
_TAG = re.compile(r"<[^<>]{1,40}>|«[^«»]{1,40}»")
_SIGN_OFF = re.compile(r"\bde\s+[A-Z0-9/]{3,10}\b", re.IGNORECASE)


def is_protocol(text: str) -> bool:
    """Only VarAC's frames and a "de CALL" sign-off: nothing for an operator to read."""
    words = _SIGN_OFF.sub(" ", _TAG.sub(" ", text))
    return not re.search(r"[A-Za-z]{2}", words)

QUERIES = {
    "broadcast": """SELECT id, broadcast_time AS time, from_callsign AS sender, to_callsign AS recipient,
                           via_callsign AS via, broadcast_message AS text, frequency, band, snr, '' AS subject,
                           0 AS urgent
                    FROM broadcast WHERE id > ? ORDER BY id""",
    "vmail": f"""SELECT id, COALESCE(received_time, creation_time) AS time, vmail_from AS sender,
                        vmail_to AS recipient, vmail_via AS via, msg AS text, frequency, delivery_band AS band,
                        delivery_snr AS snr, subject, urgent
                 FROM vmail WHERE id > ? AND folder_id = {INBOX} AND COALESCE(is_deleted, 0) = 0 ORDER BY id""",
    "datastream": f"""SELECT id, creation_time AS time, callsign AS sender, '' AS recipient, '' AS via,
                             entry AS text, NULL AS frequency, '' AS band, NULL AS snr, '' AS subject, 0 AS urgent
                      FROM datastream WHERE id > ? AND datastream_entry_type_id = {INCOMING}
                        AND COALESCE(is_deleted, 0) = 0 ORDER BY id""",
}
TYPES = {"broadcast": "BROADCAST", "vmail": "VMAIL", "datastream": "CHAT"}


class VarACSQLiteAdapter(ApplicationAdapter):
    """Reads new broadcasts, Inbox mail and incoming chat from VarAC's database."""

    application_name = "VarAC"

    def __init__(self, database_path: str | Path, lookback: timedelta | None = None) -> None:
        self.database_path = Path(database_path).expanduser()
        self.status = "Disconnected"
        self.last_error: str | None = None
        self.lookback = lookback
        self._last_ids = {table: 0 for table in QUERIES}

    def read_messages(self) -> list[Message]:
        if not str(self.database_path) or not self.database_path.is_file():
            self.status = "Database not found"
            return []
        try:
            with sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True, timeout=0.5) as connection:
                connection.row_factory = sqlite3.Row
                rows = []
                for table, query in QUERIES.items():
                    try:
                        rows += [(table, row) for row in connection.execute(query, (self._last_ids[table],))]
                    except sqlite3.OperationalError:
                        continue      # an older VarAC without this table or column
                grids = self._grids(connection, {row["sender"] for _table, row in rows if row["sender"]})
        except sqlite3.Error as error:
            self.status = "Database unavailable"
            self.last_error = str(error)
            return []
        self.status = "Connected"
        self.last_error = None
        messages = []
        for table, row in rows:
            self._last_ids[table] = max(self._last_ids[table], int(row["id"]))
            text = (row["text"] or "").replace("\r\n", "\n").strip()
            if table == "vmail" and row["subject"]:
                text = f"{row['subject']}\n{text}".strip()
            if not text or _AUTOMATIC.match(text) or is_protocol(text):
                continue
            if self.lookback and not is_recent(row["time"], self.lookback):
                continue
            sender = (row["sender"] or "").upper()
            fields = {"type": TYPES[table], "sender": sender, "message_id": f"varac:{table}:{row['id']}"}
            for key in ("recipient", "via", "band", "subject"):
                if row[key]:
                    fields[key] = str(row[key])
            if row["frequency"]:
                fields["frequency"] = str(row["frequency"])
            if row["snr"] is not None:
                fields["snr"] = str(row["snr"])
            if row["urgent"]:
                fields["priority"] = "urgent"
            if grids.get(sender):
                fields["grid"] = grids[sender]
            messages.append(Message(self.application_name, text,
                                    received_at=parse_source_time(row["time"]) or datetime.now(timezone.utc),
                                    fields=fields))
        return messages

    @staticmethod
    def _grids(connection, senders: set[str]) -> dict[str, str]:
        """Each sender's grid square: its latest CQ frame's locator, else its contact entry's."""
        grids: dict[str, str] = {}
        if not senders:
            return grids
        names = sorted({sender.upper() for sender in senders})
        marks = ",".join("?" * len(names))
        for query in (f"""SELECT UPPER(callsign), locator FROM contact WHERE UPPER(callsign) IN ({marks})
                          AND COALESCE(locator, '') != ''""",
                      f"""SELECT UPPER(from_callsign), locator FROM cqframe WHERE UPPER(from_callsign) IN ({marks})
                          AND COALESCE(locator, '') != '' ORDER BY id"""):   # later rows win
            try:
                for callsign, locator in connection.execute(query, names):
                    grids[callsign] = locator.strip()
            except sqlite3.OperationalError:
                continue
        return grids

    def close(self) -> None:
        self.status = "Closed"
