import sqlite3
from pathlib import Path

from s2_watch.analysis.base import AnalysisResult
from s2_watch.models import Message, WatchForm


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, source_application TEXT NOT NULL,
                form_name TEXT, raw_content TEXT NOT NULL, received_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS geocode_cache (
                place TEXT PRIMARY KEY,
                latitude REAL,
                longitude REAL,
                found INTEGER NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL UNIQUE,
                newsworthy INTEGER NOT NULL,
                category TEXT NOT NULL,
                urgency TEXT NOT NULL,
                summary TEXT NOT NULL,
                confidence REAL NOT NULL,
                matched_terms TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS watch_forms (
                application TEXT NOT NULL,
                name TEXT NOT NULL,
                match_text TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                use_regex INTEGER NOT NULL DEFAULT 0,
                message_type TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (application, name)
            )"""
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(watch_forms)")}
        for column in ("message_type", "sender"):
            if column not in columns:
                self.connection.execute(
                    f"ALTER TABLE watch_forms ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        self.connection.commit()

    def save_message(self, message: Message) -> int:
        cursor = self.connection.execute(
            "INSERT INTO messages(source_application, form_name, raw_content, received_at) VALUES (?, ?, ?, ?)",
            (message.source_application, message.form_name, message.raw_content, message.received_at.isoformat()),
        )
        self.connection.commit()
        return cursor.lastrowid

    def save_analysis(self, message_id: int, result: AnalysisResult) -> None:
        self.connection.execute(
            """INSERT INTO analysis_results(
                 message_id, newsworthy, category, urgency, summary, confidence, matched_terms
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                 newsworthy = excluded.newsworthy,
                 category = excluded.category,
                 urgency = excluded.urgency,
                 summary = excluded.summary,
                 confidence = excluded.confidence,
                 matched_terms = excluded.matched_terms""",
            (
                message_id,
                int(result.newsworthy),
                result.category,
                result.urgency,
                result.summary,
                result.confidence,
                "\x1f".join(result.matched_terms),
            ),
        )
        self.connection.commit()

    def count_analysis_results(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()
        return int(row[0])

    def search_history(
        self,
        text: str = "",
        category: str = "",
        urgency: str = "",
        newsworthy_only: bool = False,
    ) -> list[tuple[str, str, str, str, str, float | None]]:
        clauses = []
        parameters: list[object] = []
        if text:
            clauses.append("(m.raw_content LIKE ? OR m.form_name LIKE ?)")
            wildcard = f"%{text}%"
            parameters.extend((wildcard, wildcard))
        if category:
            clauses.append("COALESCE(a.category, '') = ?")
            parameters.append(category)
        if urgency:
            clauses.append("COALESCE(a.urgency, '') = ?")
            parameters.append(urgency)
        if newsworthy_only:
            clauses.append("COALESCE(a.newsworthy, 0) = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"""SELECT m.received_at, m.source_application, m.form_name, m.raw_content,
                       COALESCE(a.category, ''), a.confidence
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                {where}
                ORDER BY m.received_at DESC""",
            parameters,
        )
        return [tuple(row) for row in rows]

    def list_watch_forms(self, application: str | None = None) -> list[WatchForm]:
        if application is None:
            rows = self.connection.execute(
                "SELECT application, name, match_text, enabled, use_regex, message_type, sender "
                "FROM watch_forms ORDER BY application, name"
            )
        else:
            rows = self.connection.execute(
                "SELECT application, name, match_text, enabled, use_regex, message_type, sender "
                "FROM watch_forms WHERE application = ? ORDER BY name",
                (application,),
            )
        return [
            WatchForm(application, name, match_text, bool(enabled), bool(use_regex), message_type, sender)
            for application, name, match_text, enabled, use_regex, message_type, sender in rows
        ]

    def save_watch_form(self, form: WatchForm) -> None:
        self.connection.execute(
            """INSERT INTO watch_forms(
                 application, name, match_text, enabled, use_regex, message_type, sender
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(application, name) DO UPDATE SET
                 match_text = excluded.match_text,
                 enabled = excluded.enabled,
                 use_regex = excluded.use_regex,
                 message_type = excluded.message_type,
                 sender = excluded.sender""",
            (
                form.application,
                form.name,
                form.match_text,
                int(form.enabled),
                int(form.use_regex),
                form.message_type,
                form.sender,
            ),
        )
        self.connection.commit()

    def disable_all_watch_forms(self) -> None:
        self.connection.execute("UPDATE watch_forms SET enabled = 0")
        self.connection.commit()

    def delete_watch_form(self, application: str, name: str) -> None:
        self.connection.execute(
            "DELETE FROM watch_forms WHERE application = ? AND name = ?",
            (application, name),
        )
        self.connection.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            """INSERT INTO settings(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
        self.connection.commit()

    def get_geocode(self, place: str) -> tuple[float, float] | None | bool:
        row = self.connection.execute(
            "SELECT latitude, longitude, found FROM geocode_cache WHERE place = ?",
            (place,),
        ).fetchone()
        if row is None:
            return False
        if not row[2]:
            return None
        return float(row[0]), float(row[1])

    def set_geocode(self, place: str, coordinates: tuple[float, float] | None) -> None:
        self.connection.execute(
            """INSERT INTO geocode_cache(place, latitude, longitude, found)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(place) DO UPDATE SET
                 latitude = excluded.latitude,
                 longitude = excluded.longitude,
                 found = excluded.found""",
            (place, coordinates[0] if coordinates else None, coordinates[1] if coordinates else None, int(coordinates is not None)),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
