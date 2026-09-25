import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.models import Message
from s2_watch.time_utils import is_recent, parse_source_time


class JS8SuperSpotterSQLiteAdapter(ApplicationAdapter):
    """Reads new structured spots and reports from JS8SuperSpotter SQLite."""

    application_name = "JS8SuperSpotter"

    def __init__(self, database_path: str | Path, lookback: timedelta | None = None) -> None:
        self.database_path = Path(database_path).expanduser()
        self.status = "Disconnected"
        self.last_error: str | None = None
        self._last_ids = {
            "activity": 0,
            "csbrevity": 0,
            "csstatrep": 0,
            "sfm_messages": 0,
        }
        self.lookback = lookback

    def read_messages(self) -> list[Message]:
        if not self.database_path.exists():
            self.status = "Database not found"
            return []
        queries = {
            "activity": "id, type, value, call, snr, speed, spotdate",
            "csbrevity": "id, csb_from, csb_event, csb_summary, csb_detail, csb_raw, csb_timestamp",
            "csstatrep": "id, cssr_from, cssr_group, cssr_grid, cssr_prio, cssr_status, cssr_notes, cssr_timestamp",
            "sfm_messages": "id, sender, recipient, priority, body, status, created_time",
        }
        try:
            with sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True, timeout=0.5) as connection:
                connection.row_factory = sqlite3.Row
                rows = []
                for table, columns in queries.items():
                    try:
                        available = {
                            item[1] for item in connection.execute(f"PRAGMA table_info({table})")
                        }
                        timestamp_column = {
                            "activity": "spotdate",
                            "csbrevity": "csb_timestamp",
                            "csstatrep": "cssr_timestamp",
                            "sfm_messages": "created_time",
                        }[table]
                        if timestamp_column not in available:
                            columns = columns.replace(f", {timestamp_column}", ", NULL AS " + timestamp_column)
                        found = connection.execute(
                            f"SELECT {columns} FROM {table} WHERE id > ? ORDER BY id",
                            (self._last_ids[table],),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        found = []
                    rows.extend((table, row) for row in found)
        except sqlite3.Error as error:
            self.status = "Database unavailable"
            self.last_error = str(error)
            return []
        self.status = "Connected"
        self.last_error = None
        messages = []
        for table, row in sorted(rows, key=lambda item: int(item[1]["id"])):
            self._last_ids[table] = max(self._last_ids[table], int(row["id"]))
            timestamp_key = {
                "activity": "spotdate",
                "csbrevity": "csb_timestamp",
                "csstatrep": "cssr_timestamp",
                "sfm_messages": "created_time",
            }[table]
            if self.lookback and not is_recent(row[timestamp_key], self.lookback):
                continue
            fields = {"type": table.upper()}
            if table == "activity":
                content = row["value"] or ""
                fields.update({"sender": row["call"] or "", "snr": str(row["snr"] or ""), "speed": str(row["speed"] or "")})
            elif table == "csbrevity":
                content = row["csb_detail"] or row["csb_summary"] or row["csb_raw"] or ""
                fields.update({"sender": row["csb_from"] or "", "event": row["csb_event"] or "", "summary": row["csb_summary"] or ""})
            elif table == "csstatrep":
                content = row["cssr_notes"] or ""
                fields.update({"sender": row["cssr_from"] or "", "group": row["cssr_group"] or "", "grid": row["cssr_grid"] or "", "priority": str(row["cssr_prio"] or ""), "status": row["cssr_status"] or ""})
            else:
                content = row["body"] or ""
                fields.update({"sender": row["sender"] or "", "recipient": row["recipient"] or "", "priority": str(row["priority"] or ""), "status": row["status"] or ""})
            messages.append(
                Message(
                    self.application_name,
                    content,
                    received_at=parse_source_time(row[timestamp_key]) or datetime.now(timezone.utc),
                    fields=fields,
                )
            )
        return messages

    def close(self) -> None:
        self.status = "Closed"
