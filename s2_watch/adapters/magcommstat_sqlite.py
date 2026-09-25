import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.models import Message
from s2_watch.time_utils import is_recent, parse_source_time


class MagCommstatSQLiteAdapter(ApplicationAdapter):
    """Reads newly inserted MagCommstat messages from its local SQLite store."""

    application_name = "MagCommstat"

    def __init__(self, database_path: str | Path, lookback: timedelta | None = None) -> None:
        self.database_path = Path(database_path).expanduser()
        self.status = "Disconnected"
        self.last_error: str | None = None
        self._last_ids = {"messages": 0, "alerts": 0, "statrep": 0}
        self.lookback = lookback

    def read_messages(self) -> list[Message]:
        if not self.database_path.exists():
            self.status = "Database not found"
            return []
        try:
            with sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True, timeout=0.5) as connection:
                connection.row_factory = sqlite3.Row
                rows = []
                for table, columns in (
                    (
                        "messages",
                        "id, utc, from_call, to_call, body, group_name, rig_name, msg_id",
                    ),
                    (
                        "alerts",
                        "id, utc, from_call, to_call, title, message, color, alert_id",
                    ),
                    (
                        "statrep",
                        "id, utc, from_call, to_call, remarks, grid, scope, msg_id",
                    ),
                ):
                    try:
                        table_rows = connection.execute(
                            f"SELECT {columns} FROM {table} WHERE id > ? ORDER BY id",
                            (self._last_ids[table],),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        table_rows = []
                    rows.extend((table, row) for row in table_rows)
        except sqlite3.Error as error:
            self.status = "Database unavailable"
            self.last_error = str(error)
            return []
        self.status = "Connected"
        self.last_error = None
        messages: list[Message] = []
        for table, row in sorted(rows, key=lambda item: int(item[1]["id"])):
            self._last_ids[table] = max(self._last_ids[table], int(row["id"]))
            if self.lookback and not is_recent(row["utc"], self.lookback):
                continue
            fields = {"type": table.upper(), "sender": row["from_call"] or "", "recipient": row["to_call"] or ""}
            raw_content = ""
            if table == "messages":
                raw_content = row["body"] or ""
                fields.update(
                    {
                        "group": row["group_name"] or "",
                        "rig": row["rig_name"] or "",
                        "message_id": row["msg_id"] or "",
                    }
                )
            elif table == "alerts":
                raw_content = f"{row['title'] or ''}: {row['message'] or ''}".strip(": ")
                fields.update({"alert_id": row["alert_id"] or "", "severity": str(row["color"] or "")})
            else:
                raw_content = row["remarks"] or ""
                fields.update({"grid": row["grid"] or "", "scope": row["scope"] or "", "message_id": row["msg_id"] or ""})
            messages.append(
                Message(
                    source_application=self.application_name,
                    raw_content=raw_content,
                    received_at=parse_source_time(row["utc"]) or datetime.now(timezone.utc),
                    fields=fields,
                )
            )
        return messages

    def close(self) -> None:
        self.status = "Closed"
