import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from s2_watch.analysis.base import AnalysisResult
from s2_watch.dedup import DUPLICATE_WINDOW, report_text, same_report
from s2_watch.models import Message, WatchForm

URGENCY_RANK = {"low": 0, "medium": 1, "high": 2}
# Report triage status, set by the operator: one per report ("" = not yet reviewed).
REPORT_STATUSES = ("", "interest", "followup", "ignored")


@dataclass(frozen=True)
class ReportInfo:
    """The report a message belongs to: one real-world report, however many copies arrived."""

    report_id: int
    new_report: bool          # this message is the report's first copy
    copies: int
    sources: tuple[str, ...]
    status: str = ""          # see REPORT_STATUSES
    note: str = ""
    state: str = ""
    region: int | None = None  # MARS region, when known


# OSINT senders that are official (government) sources; news sites are not.
OFFICIAL_SENDERS = {"USGS", "NIFC", "NOAA SPC", "FEMA", "FAA", "U.S. Department of State", "NWS"}


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(path, timeout=10)
        # The window and the polling thread each have a connection; WAL lets one read
        # while the other writes.
        self._use_wal()
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
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY,
                report_text TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                copies INTEGER NOT NULL DEFAULT 1,
                sources TEXT NOT NULL,
                first_source TEXT NOT NULL,
                sender TEXT NOT NULL DEFAULT '',
                newsworthy INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT 'routine',
                urgency TEXT NOT NULL DEFAULT 'low',
                state TEXT NOT NULL DEFAULT '',
                region INTEGER
            )"""
        )
        report_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(reports)")}
        for column in ("status", "status_note", "status_changed", "corroboration", "exercise", "event_time"):
            if column not in report_columns:
                self._add_column(f"ALTER TABLE reports ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "official" not in report_columns:   # an official source the operator entered (confirms events)
            self._add_column("ALTER TABLE reports ADD COLUMN official INTEGER NOT NULL DEFAULT 0")
        self.connection.execute("CREATE INDEX IF NOT EXISTS reports_first_seen ON reports(first_seen)")
        # Where a report is (its most specific point) and the event it belongs to.
        for column, kind in (("lat", "REAL"), ("lon", "REAL"), ("place_level", "TEXT NOT NULL DEFAULT ''"),
                             ("event_id", "INTEGER")):
            if column not in report_columns:
                self._add_column(f"ALTER TABLE reports ADD COLUMN {column} {kind}")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                hazards TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '',
                region INTEGER,
                lat REAL,
                lon REAL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                incident TEXT NOT NULL DEFAULT '',
                terms TEXT NOT NULL DEFAULT ''
            )"""
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS events_last_seen ON events(last_seen)")
        # Closed by an all-clear report or the operator (closed_by "report" / "operator").
        event_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(events)")}
        for column in ("closed_at", "close_reason", "closed_by"):
            if column not in event_columns:
                self._add_column(f"ALTER TABLE events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        # Briefings and reports the operator saved in S2 Watch (the draft text, to reopen).
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL,
                dtg TEXT NOT NULL,
                text TEXT NOT NULL,
                report_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        # Intelligence the operator entered (summaries cleared for release, e.g. from HSIN or
        # a fusion center): delivered as OSINT items of type "ANALYST ENTRY".
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS analyst_entries (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                happened_at TEXT NOT NULL,
                source TEXT NOT NULL,
                handling TEXT NOT NULL,
                official INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL,
                urgency TEXT NOT NULL,
                place TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                removed INTEGER NOT NULL DEFAULT 0
            )"""
        )
        # The operator's location corrections: a phrase ("BIXBY") or one message ("#msg:<hash>")
        # and where it really is.
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS place_corrections (
                phrase TEXT PRIMARY KEY,
                level TEXT NOT NULL,
                label TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                state TEXT NOT NULL DEFAULT '',
                region INTEGER,
                created_at TEXT NOT NULL
            )"""
        )
        # Radio traffic no watch form kept, read by the model in the background (missed_news).
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS missed_checks (
                key TEXT PRIMARY KEY,
                checked_at TEXT NOT NULL,
                source_application TEXT NOT NULL,
                raw_content TEXT NOT NULL,
                received_at TEXT NOT NULL,
                fields_json TEXT NOT NULL DEFAULT '',
                newsworthy INTEGER NOT NULL,
                category TEXT NOT NULL,
                urgency TEXT NOT NULL,
                summary TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL
            )"""
        )
        missed_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(missed_checks)")}
        for column, kind in (("matched_terms", "TEXT NOT NULL DEFAULT ''"), ("decided_at", "TEXT NOT NULL DEFAULT ''")):
            if column not in missed_columns:
                self._add_column(f"ALTER TABLE missed_checks ADD COLUMN {column} {kind}")
        self.connection.execute("CREATE INDEX IF NOT EXISTS reports_event_id ON reports(event_id)")
        message_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(messages)")}
        if "source_key" not in message_columns:
            self._add_column("ALTER TABLE messages ADD COLUMN source_key TEXT")
        if "report_id" not in message_columns:
            self._add_column("ALTER TABLE messages ADD COLUMN report_id INTEGER")
        if "fields_json" not in message_columns:
            # The source's structured fields (grid, sender, coordinates...), so a report
            # can be shown and mapped again at launch without re-reading its source.
            self._add_column("ALTER TABLE messages ADD COLUMN fields_json TEXT NOT NULL DEFAULT ''")
        self.connection.execute("CREATE INDEX IF NOT EXISTS messages_report_id ON messages(report_id)")
        analysis_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(analysis_results)")}
        if "analyzer" not in analysis_columns:
            # Which analyzer produced the result ("rules", "ollama:<model>"), so a message
            # read again isn't analysed again by the same one.
            self._add_column("ALTER TABLE analysis_results ADD COLUMN analyzer TEXT NOT NULL DEFAULT ''")
        self._remove_repeated_messages()
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS messages_source_key ON messages(source_key)"
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(watch_forms)")}
        for column in ("message_type", "sender"):
            if column not in columns:
                self._add_column(
                    f"ALTER TABLE watch_forms ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        self.connection.commit()

    def _add_column(self, statement: str) -> None:
        """Run an ALTER TABLE ... ADD COLUMN. The window and the background threads open the
        database together, so another connection may have just added the same column."""
        try:
            self.connection.execute(statement)
        except sqlite3.OperationalError as error:
            if "duplicate column name" not in str(error):
                raise

    def _use_wal(self) -> None:
        # Switching to WAL needs a moment of exclusive access and doesn't wait for it; the
        # window, the polling thread and the background check open the database together.
        # Once a database is in WAL mode it stays so, and there is nothing to switch.
        for attempt in range(50):
            try:
                if self.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    self.connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError:
                if attempt == 49:
                    raise
                time.sleep(0.1)

    def _remove_repeated_messages(self) -> None:
        """One-time cleanup: earlier versions saved a message again on every launch."""
        if self.get_setting("migrations.repeated_messages_removed") == "1":
            return
        self.connection.execute(
            """DELETE FROM analysis_results WHERE message_id IN (
                 SELECT id FROM messages WHERE id NOT IN (
                   SELECT MIN(id) FROM messages GROUP BY source_application, raw_content, received_at))"""
        )
        self.connection.execute(
            """DELETE FROM messages WHERE id NOT IN (
                 SELECT MIN(id) FROM messages GROUP BY source_application, raw_content, received_at)"""
        )
        self.set_setting("migrations.repeated_messages_removed", "1")

    def save_message(self, message: Message, source_key: str | None = None, legacy_key: str | None = None) -> int:
        """Save a matched message once; a message read again returns its existing id.

        ``legacy_key`` is the key an earlier version saved the message under; a match is
        re-keyed so the message is not saved (and counted) a second time.
        """
        if source_key is not None:
            row = self.connection.execute(
                "SELECT id FROM messages WHERE source_key = ?", (source_key,)
            ).fetchone()
            if row:
                self._backfill_fields(int(row[0]), message)
                return int(row[0])
            if legacy_key and legacy_key != source_key:
                row = self.connection.execute(
                    "SELECT id FROM messages WHERE source_key = ?", (legacy_key,)
                ).fetchone()
                if row:
                    self.connection.execute("UPDATE messages SET source_key = ? WHERE id = ?", (source_key, row[0]))
                    self.connection.commit()
                    self._backfill_fields(int(row[0]), message)
                    return int(row[0])
            # Saved by an earlier version, before messages had a source key.
            row = self.connection.execute(
                """SELECT id FROM messages WHERE source_key IS NULL AND source_application = ?
                   AND raw_content = ? AND received_at = ?""",
                (message.source_application, message.raw_content, message.received_at.isoformat()),
            ).fetchone()
            if row:
                self.connection.execute(
                    "UPDATE messages SET source_key = ?, form_name = ? WHERE id = ?",
                    (source_key, message.form_name, row[0]),
                )
                self.connection.commit()
                return int(row[0])
        cursor = self.connection.execute(
            """INSERT INTO messages(source_application, form_name, raw_content, received_at, source_key, fields_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (message.source_application, message.form_name, message.raw_content,
             message.received_at.isoformat(), source_key, json.dumps(message.fields) if message.fields else ""),
        )
        self.connection.commit()
        return cursor.lastrowid

    def assign_report(
        self,
        message_id: int,
        message: Message,
        result: AnalysisResult | None,
        state: str = "",
        region: int | None = None,
    ) -> ReportInfo:
        """Link a saved message to its report, starting a new report if it is the first copy."""
        from s2_watch.analysis.ollama import readable_text
        from s2_watch.event_time import event_time
        from s2_watch.exercise import exercise_kind

        kind = exercise_kind(message)
        stated = None if message.source_application == "OSINT" else event_time(readable_text(message), message.received_at)
        stated_at = utc_iso(stated) if stated else ""
        official = int(message.fields.get("official") == "1")
        row = self.connection.execute(
            "SELECT report_id FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row and row[0] is not None:
            report_id = int(row[0])
            if result is not None:
                # The report follows the latest analysis of its first copy (a later, better
                # analyzer such as Ollama can find a rules match was routine chat).
                self.connection.execute(
                    """UPDATE reports SET newsworthy = ?, category = ?, urgency = ?, exercise = ?, event_time = ?,
                         official = ?
                       WHERE id = ? AND (SELECT MIN(id) FROM messages WHERE report_id = ?) = ?""",
                    (int(result.newsworthy), result.category, result.urgency, kind, stated_at, official, report_id,
                     report_id, message_id))
                self.connection.commit()
            return self._report_info(report_id, new_report=False)
        text = report_text(message.raw_content)
        seen = message.received_at if message.received_at.tzinfo else message.received_at.replace(tzinfo=timezone.utc)
        low, high = utc_iso(seen - DUPLICATE_WINDOW), utc_iso(seen + DUPLICATE_WINDOW)
        candidates = self.connection.execute(
            "SELECT id, report_text FROM reports WHERE first_seen BETWEEN ? AND ? ORDER BY first_seen",
            (low, high),
        ).fetchall()
        match = next((rid for rid, other in candidates if other == text), None)
        if match is None:
            match = next((rid for rid, other in candidates if same_report(text, other)), None)
        newsworthy = bool(result and result.newsworthy)
        category = result.category if newsworthy else "routine"
        urgency = result.urgency if result else "low"
        sender = message.fields.get("sender", message.fields.get("from", ""))
        if match is None:
            cursor = self.connection.execute(
                """INSERT INTO reports(report_text, first_seen, last_seen, copies, sources, first_source,
                     sender, newsworthy, category, urgency, state, region, exercise, event_time, official)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (text, utc_iso(seen), utc_iso(seen), message.source_application, message.source_application,
                 sender, int(newsworthy), category, urgency, state, region, kind, stated_at, official),
            )
            report_id, new_report = cursor.lastrowid, True
        else:
            report_id, new_report = match, False
            first_seen, last_seen, sources, was_news, old_urgency, old_state, old_region = self.connection.execute(
                "SELECT first_seen, last_seen, sources, newsworthy, urgency, state, region FROM reports WHERE id = ?",
                (report_id,),
            ).fetchone()
            source_list = sources.split(",")
            if message.source_application not in source_list:
                source_list.append(message.source_application)
            self.connection.execute(
                """UPDATE reports SET copies = copies + 1, sources = ?, first_seen = ?, last_seen = ?,
                     newsworthy = ?, category = CASE WHEN ? AND NOT newsworthy THEN ? ELSE category END,
                     urgency = ?, state = ?, region = ?
                   WHERE id = ?""",
                (",".join(source_list), min(first_seen, utc_iso(seen)), max(last_seen, utc_iso(seen)),
                 int(was_news or newsworthy), int(newsworthy), category,
                 max(old_urgency, urgency, key=lambda value: URGENCY_RANK.get(value, 0)),
                 old_state or state, old_region if old_region is not None else region, report_id),
            )
        self.connection.execute("UPDATE messages SET report_id = ? WHERE id = ?", (report_id, message_id))
        self.connection.commit()
        return self._report_info(report_id, new_report)

    @staticmethod
    def _region_filter(regions, column: str = "region") -> tuple[str, list]:
        """SQL limiting reports to MARS ``regions`` (None = every report)."""
        if not regions:
            return "", []
        return f" AND {column} IN ({','.join('?' * len(regions))})", sorted(regions)

    def report_totals(self, since: str, regions=None) -> tuple[int, int, int]:
        """(reports, newsworthy reports, copies received) for reports first seen since ``since``."""
        clause, values = self._region_filter(regions)
        reports, newsworthy, copies = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(newsworthy), 0), COALESCE(SUM(copies), 0) FROM reports"
            " WHERE first_seen >= ? AND status != 'ignored'" + clause,
            (since, *values),
        ).fetchone()
        return int(reports), int(newsworthy), int(copies)

    def newsworthy_reports(self, since: str) -> list[dict]:
        rows = self.connection.execute(
            """SELECT id, first_seen, category, urgency, state, region, first_source, sender, copies
               FROM reports WHERE newsworthy = 1 AND first_seen >= ? AND status != 'ignored'
               ORDER BY first_seen""",
            (since,),
        ).fetchall()
        keys = ("id", "first_seen", "category", "urgency", "state", "region", "first_source", "sender", "copies")
        return [dict(zip(keys, row)) for row in rows]

    def copies_by_source(self, since: str, regions=None) -> dict[str, int]:
        """Copies of newsworthy reports received from each source application."""
        clause, values = self._region_filter(regions, "r.region")
        rows = self.connection.execute(
            """SELECT m.source_application, COUNT(*) FROM messages m JOIN reports r ON r.id = m.report_id
               WHERE r.newsworthy = 1 AND r.first_seen >= ? AND r.status != 'ignored'""" + clause +
            " GROUP BY m.source_application",
            (since, *values),
        ).fetchall()
        return {source: int(count) for source, count in rows}

    def _report_info(self, report_id: int, new_report: bool) -> ReportInfo:
        copies, sources, status, note, state, region = self.connection.execute(
            "SELECT copies, sources, status, status_note, state, region FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        return ReportInfo(report_id, new_report, int(copies), tuple(sources.split(",")), status, note,
                          state or "", int(region) if region is not None else None)

    def set_form_name(self, message_id: int, form_name: str) -> None:
        self.connection.execute("UPDATE messages SET form_name = ? WHERE id = ?", (form_name, message_id))
        self.connection.commit()

    def _backfill_fields(self, message_id: int, message: Message) -> None:
        """Messages saved before 0.12.0 have no fields; add them when the source is read again."""
        if message.fields:
            cursor = self.connection.execute(
                "UPDATE messages SET fields_json = ? WHERE id = ? AND fields_json = ''",
                (json.dumps(message.fields), message_id),
            )
            if cursor.rowcount:
                self.connection.commit()

    def report_info(self, report_id: int) -> ReportInfo:
        return self._report_info(report_id, new_report=False)

    def recent_reports(self, since: datetime, limit: int = 1000) -> list[tuple[int, Message, AnalysisResult | None]]:
        """Each report seen since ``since``, newest first, as its first saved copy and analysis.

        This is what the Newsworthiness table and map show at launch, before (and whether
        or not) the sources are read again.
        """
        rows = self.connection.execute(
            """SELECT r.id, m.source_application, m.form_name, m.raw_content, m.received_at, m.fields_json,
                      a.newsworthy, a.category, a.urgency, a.summary, a.confidence, a.matched_terms
               FROM reports r
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               LEFT JOIN analysis_results a ON a.message_id = m.id
               WHERE r.last_seen >= ?
               ORDER BY r.last_seen DESC LIMIT ?""",
            (utc_iso(since), limit),
        ).fetchall()
        reports = []
        for (report_id, application, form_name, raw_content, received_at, fields_json,
             newsworthy, category, urgency, summary, confidence, matched_terms) in rows:
            try:
                fields = json.loads(fields_json) if fields_json else {}
            except ValueError:
                fields = {}
            when = datetime.fromisoformat(received_at)
            message = Message(application, raw_content, received_at=when if when.tzinfo else when.replace(tzinfo=timezone.utc),
                              form_name=form_name, fields={str(k): str(v) for k, v in fields.items()})
            result = None
            if newsworthy is not None:
                result = AnalysisResult(bool(newsworthy), category, urgency, summary, float(confidence),
                                        tuple(term for term in (matched_terms or "").split("\x1f") if term))
            reports.append((int(report_id), message, result))
        return reports

    def earliest_received_at(self, message_id: int, received_at: datetime) -> datetime:
        """The earliest time known for a message, keeping the stored one in step.

        A source without timestamps is stamped with the time it was read, so reading it
        again (every launch) must not move the message to now; a corrected, earlier time
        from the source moves the message and its report back.
        """
        row = self.connection.execute(
            "SELECT received_at, report_id FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if not row:
            return received_at
        candidate = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
        stored = datetime.fromisoformat(row[0])
        stored = stored if stored.tzinfo else stored.replace(tzinfo=timezone.utc)
        if stored <= candidate:
            return stored
        self.connection.execute("UPDATE messages SET received_at = ? WHERE id = ?", (candidate.isoformat(), message_id))
        if row[1] is not None:
            self.connection.execute(
                "UPDATE reports SET first_seen = ? WHERE id = ? AND first_seen > ?",
                (utc_iso(candidate), row[1], utc_iso(candidate)),
            )
        self.connection.commit()
        return candidate

    def detach_from_report(self, message_id: int) -> None:
        """Unlink a message from its report, deleting the report if nothing else belongs to it."""
        row = self.connection.execute("SELECT report_id FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row or row[0] is None:
            return
        self.connection.execute("UPDATE messages SET report_id = NULL WHERE id = ?", (message_id,))
        remaining = self.connection.execute("SELECT COUNT(*) FROM messages WHERE report_id = ?", (row[0],)).fetchone()[0]
        if remaining:
            self.connection.execute("UPDATE reports SET copies = ? WHERE id = ?", (remaining, row[0]))
        else:
            self.connection.execute("DELETE FROM reports WHERE id = ?", (row[0],))
        self.connection.execute("DELETE FROM analysis_results WHERE message_id = ?", (message_id,))
        self.connection.commit()

    def set_corroboration(self, report_id: int, text: str) -> None:
        self.connection.execute("UPDATE reports SET corroboration = ? WHERE id = ?", (text, report_id))
        self.connection.commit()

    # -- saved reports and briefings ---------------------------------------------------

    def save_draft(self, kind: str, text: str, draft_id: int | None = None, report_id: int | None = None) -> int:
        """Save (or update) a briefing or report draft; its id."""
        from s2_watch.s2_report import parse_report

        report = parse_report(text)
        now = utc_iso(datetime.now(timezone.utc))
        if draft_id is not None and self.connection.execute("SELECT 1 FROM drafts WHERE id = ?", (draft_id,)).fetchone():
            self.connection.execute(
                "UPDATE drafts SET subject = ?, dtg = ?, text = ?, updated_at = ? WHERE id = ?",
                (report.subject, report.dtg, text, now, draft_id))
        else:
            draft_id = self.connection.execute(
                """INSERT INTO drafts(kind, subject, dtg, text, report_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (kind, report.subject, report.dtg, text, report_id, now, now)).lastrowid
        self.connection.commit()
        return int(draft_id)

    def list_drafts(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id, kind, subject, dtg, report_id, created_at, updated_at FROM drafts ORDER BY updated_at DESC"
        ).fetchall()
        keys = ("id", "kind", "subject", "dtg", "report_id", "created_at", "updated_at")
        return [dict(zip(keys, row)) for row in rows]

    def get_draft(self, draft_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT id, kind, subject, dtg, text, report_id, updated_at FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        keys = ("id", "kind", "subject", "dtg", "text", "report_id", "updated_at")
        return dict(zip(keys, row)) if row else None

    def delete_draft(self, draft_id: int) -> None:
        self.connection.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        self.connection.commit()

    # -- possible misses ---------------------------------------------------------------

    def missed_checked(self, key: str) -> bool:
        return self.connection.execute("SELECT 1 FROM missed_checks WHERE key = ?", (key,)).fetchone() is not None

    def save_missed_check(self, key: str, message: Message, result: AnalysisResult, status: str) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO missed_checks(key, checked_at, source_application, raw_content, received_at,
                 fields_json, newsworthy, category, urgency, summary, confidence, status, matched_terms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, utc_iso(datetime.now(timezone.utc)), message.source_application, message.raw_content,
             message.received_at.isoformat(), json.dumps(message.fields) if message.fields else "",
             int(result.newsworthy), result.category, result.urgency, result.summary, result.confidence, status,
             "\x1f".join(result.matched_terms)),
        )
        self.connection.commit()

    def possible_misses(self, status: str = "new", since: datetime | None = None) -> list[tuple[str, Message, AnalysisResult]]:
        """Messages the model thinks are news though no watch form kept them, newest first
        (heard since ``since``, when given)."""
        rows = self.connection.execute(
            """SELECT key, source_application, raw_content, received_at, fields_json, newsworthy, category, urgency,
                      summary, confidence, matched_terms FROM missed_checks WHERE status = ? ORDER BY received_at DESC""",
            (status,),
        ).fetchall()
        misses = []
        for key, application, raw, received, fields_json, newsworthy, category, urgency, summary, confidence, terms in rows:
            heard = datetime.fromisoformat(received)
            heard = heard if heard.tzinfo else heard.replace(tzinfo=timezone.utc)
            if since is not None and heard < since:
                continue   # older than the watch period: left out of the list, kept in the database
            try:
                fields = json.loads(fields_json) if fields_json else {}
            except ValueError:
                fields = {}
            when = datetime.fromisoformat(received)
            message = Message(application, raw, received_at=when if when.tzinfo else when.replace(tzinfo=timezone.utc),
                              fields={str(k): str(v) for k, v in fields.items()})
            misses.append((key, message, AnalysisResult(bool(newsworthy), category, urgency, summary, float(confidence),
                                                        tuple(term for term in (terms or "").split("\x1f") if term))))
        return misses

    def set_missed_status(self, key: str, status: str, corrected: AnalysisResult | None = None) -> None:
        """Record the operator's decision ("added", "dismissed", "deleted", back to "new"),
        with their corrected verdict when they added it."""
        now = utc_iso(datetime.now(timezone.utc)) if status in ("added", "dismissed") else ""
        if corrected is not None:
            self.connection.execute(
                """UPDATE missed_checks SET status = ?, decided_at = ?, newsworthy = ?, category = ?, urgency = ?,
                     summary = ? WHERE key = ?""",
                (status, now, int(corrected.newsworthy), corrected.category, corrected.urgency, corrected.summary, key))
        else:
            self.connection.execute("UPDATE missed_checks SET status = ?, decided_at = ? WHERE key = ?", (status, now, key))
        self.connection.commit()

    def keyword_matches(self, keyword: str, limit: int = 2000) -> tuple[int, int]:
        """How many of the latest radio messages a keyword would catch: (matched, looked at).
        The background check's record of unmatched traffic plus the kept messages make the sample."""
        keyword = keyword.strip().lower()
        if not keyword:
            return 0, 0
        pattern = re.compile(r"\b" + re.escape(keyword), re.IGNORECASE)
        texts = [row[0] for row in self.connection.execute(
            """SELECT raw_content FROM (
                 SELECT raw_content, checked_at AS at FROM missed_checks
                 UNION ALL SELECT raw_content, received_at AS at FROM messages WHERE source_application != 'OSINT')
               ORDER BY at DESC LIMIT ?""", (limit,)).fetchall()]
        return sum(bool(pattern.search(text)) for text in texts), len(texts)

    def keyword_noise(self, since: datetime | None = None) -> list[dict]:
        """For each category and matched keyword: how many newsworthy reports it flagged and
        how many of those the operator marked Ignored (exercise and test traffic aside)."""
        rows = self.connection.execute(
            """SELECT r.category, a.matched_terms, r.status, r.first_source
               FROM reports r
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               JOIN analysis_results a ON a.message_id = m.id
               WHERE r.newsworthy = 1 AND r.exercise = '' AND r.first_seen >= ?""",
            (utc_iso(since) if since else "",)).fetchall()
        counts: dict[tuple[str, str], list[int]] = {}
        for category, terms, status, source in rows:
            for term in {term for term in (terms or "").split("\x1f") if term}:
                entry = counts.setdefault((category, term), [0, 0, 0])   # reports, ignored radio, ignored feeds
                entry[0] += 1
                if status == "ignored":
                    entry[2 if source == "OSINT" else 1] += 1
        return sorted(({"category": category, "keyword": term, "reports": total, "ignored": radio + feeds,
                        "ignored_radio": radio, "ignored_feeds": feeds}
                       for (category, term), (total, radio, feeds) in counts.items()),
                      key=lambda row: (-row["ignored"] / row["reports"], -row["ignored"], row["keyword"]))

    def missed_decisions(self, limit: int = 8) -> list[tuple[str, str]]:
        """The operator's latest decisions as (message, verdict) examples for the model."""
        rows = self.connection.execute(
            """SELECT source_application, raw_content, status, category FROM missed_checks
               WHERE status IN ('added', 'dismissed') AND decided_at != '' ORDER BY decided_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        from s2_watch.analysis.ollama import readable_text

        return [(" ".join(readable_text(Message(application, raw)).split()),
                 f"news ({category})" if status == "added" else "not news")
                for application, raw, status, category in rows]

    def missed_counts(self) -> dict[str, int]:
        return dict(self.connection.execute("SELECT status, COUNT(*) FROM missed_checks GROUP BY status").fetchall())

    # -- events ----------------------------------------------------------------------

    def assign_event(self, report_id: int, category: str, hazards: set[str], place, title: str,
                     incident: str = "", terms: set[str] | None = None, text: str = "") -> int:
        """Put a newsworthy report in its event (an existing one, or a new one); the event id.

        ``text`` is the report's words when they may close the event ("power restored",
        "warning cancelled"); a later report reopens a closed event.
        """
        from s2_watch.events import (EVENT_GAP, INCIDENT_GAP, EventCandidate, Place, all_clear, choose_event,
                                     clean_title)

        terms = terms or set()
        title = clean_title(title, place)

        row = self.connection.execute(
            "SELECT event_id, first_seen, last_seen FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise KeyError(report_id)
        event_id, first_seen, last_seen = row
        if event_id is not None:   # another copy of a report already placed: keep the event current
            self.connection.execute("UPDATE events SET last_seen = MAX(last_seen, ?) WHERE id = ?", (last_seen, event_id))
            self.connection.commit()
            return int(event_id)
        seen = datetime.fromisoformat(first_seen)
        self.connection.execute("UPDATE reports SET lat = ?, lon = ?, place_level = ? WHERE id = ?",
                                (place.lat, place.lon, place.level, report_id))
        rows = self.connection.execute(
            """SELECT id, category, hazards, state, region, lat, lon, first_seen, last_seen, incident, terms FROM events
               WHERE category = ? AND ((last_seen >= ? AND first_seen <= ?)
                                       OR (incident != '' AND incident = ? AND last_seen >= ?))""",
            (category, utc_iso(seen - EVENT_GAP), utc_iso(seen + EVENT_GAP), incident, utc_iso(seen - INCIDENT_GAP)),
        ).fetchall()
        candidates = [EventCandidate(row[0], row[1], {h for h in row[2].split(",") if h},
                                     Place(row[5], row[6], "", row[3], row[4]),
                                     datetime.fromisoformat(row[7]), datetime.fromisoformat(row[8]),
                                     row[9], {t for t in row[10].split(",") if t}) for row in rows]
        chosen = choose_event(candidates, category, hazards, place, seen, incident, terms)
        if chosen is None:
            cursor = self.connection.execute(
                """INSERT INTO events(title, category, hazards, state, region, lat, lon, first_seen, last_seen,
                                     incident, terms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (title, category, ",".join(sorted(hazards)), place.state, place.region, place.lat, place.lon,
                 first_seen, last_seen, incident, ",".join(sorted(terms))),
            )
            event_id = cursor.lastrowid
        else:
            event_id = chosen.event_id
            merged = ",".join(sorted(chosen.hazards | hazards))
            # An official incident's latest update gives the event its title (acres, containment).
            if incident and chosen.incident == incident:
                self.connection.execute("UPDATE events SET title = ? WHERE id = ?", (title, event_id))
            self.connection.execute(
                """UPDATE events SET hazards = ?, first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?),
                     state = CASE WHEN state = '' THEN ? ELSE state END,
                     region = COALESCE(region, ?),
                     lat = COALESCE(lat, ?), lon = COALESCE(lon, ?),
                     incident = CASE WHEN incident = '' THEN ? ELSE incident END,
                     terms = ?
                   WHERE id = ?""",
                (merged, first_seen, last_seen, place.state, place.region, place.lat, place.lon, incident,
                 ",".join(sorted(chosen.terms or terms)), event_id),   # the first report's words: a growing
                                                                        # set would match ever more stories
            )
        self.connection.execute("UPDATE reports SET event_id = ? WHERE id = ?", (event_id, report_id))
        clear = all_clear(text) if text else ""
        if clear:
            self.connection.execute(
                """UPDATE events SET closed_at = ?, close_reason = ?, closed_by = 'report'
                   WHERE id = ? AND (closed_at = '' OR closed_at < ?)""",
                (first_seen, clear, event_id, first_seen))
        else:   # news after the closure: it isn't over after all
            self.connection.execute(
                "UPDATE events SET closed_at = '', close_reason = '', closed_by = '' WHERE id = ? AND closed_at != '' "
                "AND closed_at < ?", (event_id, first_seen))
        self.connection.commit()
        return int(event_id)

    def add_analyst_entry(self, happened_at: datetime, source: str, handling: str, official: bool, category: str,
                          urgency: str, place: str, text: str) -> int:
        cursor = self.connection.execute(
            """INSERT INTO analyst_entries(created_at, happened_at, source, handling, official, category, urgency,
                                           place, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (utc_iso(datetime.now(timezone.utc)), utc_iso(happened_at), source.strip(), handling.strip(),
             int(official), category, urgency, place.strip(), text.strip()))
        self.connection.commit()
        return int(cursor.lastrowid)

    def analyst_entries(self, since: datetime | None = None, include_removed: bool = False) -> list[dict]:
        keys = ("id", "created_at", "happened_at", "source", "handling", "official", "category", "urgency", "place",
                "text", "removed")
        rows = self.connection.execute(
            f"""SELECT {", ".join(keys)} FROM analyst_entries WHERE happened_at >= ? {"" if include_removed else
                "AND removed = 0"} ORDER BY happened_at DESC""", (utc_iso(since) if since else "",)).fetchall()
        return [dict(zip(keys, row)) for row in rows]

    def remove_analyst_entry(self, entry_id: int, message_key: str) -> None:
        """Withdraw an entry: it leaves the news lists and events (its report stays in history)."""
        self.connection.execute("UPDATE analyst_entries SET removed = 1 WHERE id = ?", (entry_id,))
        self.connection.execute(
            "UPDATE reports SET newsworthy = 0 WHERE id IN (SELECT report_id FROM messages WHERE source_key = ?)",
            (message_key,))
        self.connection.commit()

    def save_place_correction(self, phrase: str, location) -> None:
        """Remember where ``phrase`` (or one message, "#msg:<hash>") really is."""
        self.connection.execute(
            """INSERT INTO place_corrections(phrase, level, label, lat, lon, state, region, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(phrase) DO UPDATE SET level = excluded.level, label = excluded.label, lat = excluded.lat,
                 lon = excluded.lon, state = excluded.state, region = excluded.region,
                 created_at = excluded.created_at""",
            (phrase, location.level, location.label, location.lat, location.lon, location.state or "", location.region,
             utc_iso(datetime.now(timezone.utc))))
        self.connection.commit()

    def place_corrections(self) -> list[tuple[str, object]]:
        from s2_watch.locating import ResolvedLocation

        return [(phrase, ResolvedLocation(level, lat, lon, label, state or None, region))
                for phrase, level, label, lat, lon, state, region in self.connection.execute(
                    """SELECT phrase, level, label, lat, lon, state, region FROM place_corrections
                       ORDER BY length(phrase) DESC""")]

    def delete_place_correction(self, phrase: str) -> None:
        self.connection.execute("DELETE FROM place_corrections WHERE phrase = ?", (phrase,))
        self.connection.commit()

    def correct_report_location(self, report_id: int, location) -> None:
        """Put a report (and its event, when the event's place came from it) where it really is."""
        row = self.connection.execute("SELECT lat, lon, event_id FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            return
        old_lat, old_lon, event_id = row
        self.connection.execute(
            "UPDATE reports SET lat = ?, lon = ?, place_level = ?, state = ?, region = ? WHERE id = ?",
            (location.lat, location.lon, location.level, location.state or "", location.region, report_id))
        if event_id is not None:
            self.connection.execute(
                """UPDATE events SET lat = ?, lon = ?, state = ?, region = ?
                   WHERE id = ? AND ((lat IS NULL AND lon IS NULL) OR (lat = ? AND lon = ?)
                                     OR (SELECT COUNT(*) FROM reports WHERE event_id = ?) = 1)""",
                (location.lat, location.lon, location.state or "", location.region, event_id,
                 old_lat, old_lon, event_id))
        self.connection.commit()

    def close_events_from_history(self, since: datetime) -> int:
        """Close open events whose latest report is an all-clear (reports placed before events
        could close). Only radio and official reports close events, not news. The number closed."""
        from s2_watch.analysis.ollama import readable_text
        from s2_watch.events import all_clear

        rows = self.connection.execute(
            """SELECT r.event_id, r.first_seen, m.source_application, m.raw_content, r.sender, r.official
               FROM reports r
               JOIN events e ON e.id = r.event_id
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               WHERE r.newsworthy = 1 AND e.closed_at = '' AND e.last_seen >= ?
               ORDER BY r.event_id, r.first_seen""", (utc_iso(since),)).fetchall()
        closing: dict[int, tuple[str, str] | None] = {}
        for event_id, first_seen, application, raw, sender, entered in rows:
            may_close = application != "OSINT" or sender in OFFICIAL_SENDERS or bool(entered)
            clear = all_clear(readable_text(Message(application, raw))) if may_close else ""
            if clear:
                closing[event_id] = (first_seen, clear)
            elif closing.get(event_id) is not None:
                closing[event_id] = None          # more news after the all-clear: still open
        closed = 0
        for event_id, found in closing.items():
            if found is not None:
                self.connection.execute(
                    "UPDATE events SET closed_at = ?, close_reason = ?, closed_by = 'report' WHERE id = ?",
                    (found[0], found[1], event_id))
                closed += 1
        self.connection.commit()
        return closed

    def close_event(self, event_id: int, reason: str = "", when: datetime | None = None) -> None:
        """The operator closes an event (a later report reopens it)."""
        when = when or datetime.now(timezone.utc)
        self.connection.execute(
            "UPDATE events SET closed_at = ?, close_reason = ?, closed_by = 'operator' WHERE id = ?",
            (utc_iso(when), reason.strip() or "closed by the operator", event_id))
        self.connection.commit()

    def reopen_event(self, event_id: int) -> None:
        self.connection.execute(
            "UPDATE events SET closed_at = '', close_reason = '', closed_by = '' WHERE id = ?", (event_id,))
        self.connection.commit()

    def reports_needing_events(self, since: datetime) -> list[tuple[int, Message, str, str]]:
        """Newsworthy reports since ``since`` not yet in an event: (id, first copy, category, summary)."""
        rows = self.connection.execute(
            """SELECT r.id, m.source_application, m.raw_content, m.received_at, m.fields_json, r.category,
                      COALESCE(a.summary, '')
               FROM reports r
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               LEFT JOIN analysis_results a ON a.message_id = m.id
               WHERE r.newsworthy = 1 AND r.event_id IS NULL AND r.first_seen >= ?
               ORDER BY r.first_seen""",
            (utc_iso(since),),
        ).fetchall()
        result = []
        for report_id, application, raw, received, fields_json, category, summary in rows:
            try:
                fields = json.loads(fields_json) if fields_json else {}
            except ValueError:
                fields = {}
            when = datetime.fromisoformat(received)
            message = Message(application, raw, received_at=when if when.tzinfo else when.replace(tzinfo=timezone.utc),
                              fields={str(k): str(v) for k, v in fields.items()})
            result.append((int(report_id), message, category, summary))
        return result

    def events_since(self, since: datetime, regions: set[int] | None = None):
        """Events active since ``since``, newest activity first, with their reports summarised."""
        from s2_watch.events import EventSummary

        rows = self.connection.execute(
            """SELECT e.id, e.title, e.category, e.state, e.region, e.first_seen, e.last_seen,
                      r.first_seen, r.sources, r.first_source, r.urgency, r.corroboration, r.status, r.lat, r.lon,
                      r.sender, e.closed_at, e.close_reason, e.closed_by, r.exercise, r.official
               FROM events e JOIN reports r ON r.event_id = e.id
               WHERE e.last_seen >= ? AND r.newsworthy = 1
               ORDER BY e.last_seen DESC, r.first_seen""",
            (utc_iso(since),),
        ).fetchall()
        rank = {"low": 1, "medium": 2, "high": 3}
        events: dict[int, dict] = {}
        for (event_id, title, category, state, region, first, last, seen, sources, first_source, urgency,
             corroboration, status, lat, lon, sender, closed_at, close_reason, closed_by, exercise, entered) in rows:
            event = events.setdefault(event_id, {
                "title": title, "category": category, "state": state, "region": region,
                "first": datetime.fromisoformat(first), "last": datetime.fromisoformat(last),
                "reports": 0, "sources": [], "urgency": "low", "official": "", "radio": None,
                "official_time": None, "lead": None, "statuses": [], "points": [],
                "closed_at": datetime.fromisoformat(closed_at) if closed_at else None,
                "close_reason": close_reason, "closed_by": closed_by, "exercise": False, "voices": set()})
            event["reports"] += 1
            # Who said it: a station's call sign or an outlet's name (unknown senders each count).
            event["voices"].add((first_source == "OSINT", (sender or "").upper() or f"#{len(event['voices'])}:{seen}"))
            event["exercise"] = event["exercise"] or exercise == "exercise"
            for source in sources.split(","):
                if source and source not in event["sources"]:
                    event["sources"].append(source)
            if rank.get(urgency, 0) > rank.get(event["urgency"], 0):
                event["urgency"] = urgency
            when = datetime.fromisoformat(seen)
            if first_source == "OSINT" and (sender in OFFICIAL_SENDERS or entered):
                event["official"] = event["official"] or sender
                if event["official_time"] is None or when < event["official_time"]:
                    event["official_time"] = when
            elif first_source == "OSINT":
                pass   # news coverage: open-source, but not official confirmation
            else:
                if event["radio"] is None or when < event["radio"]:
                    event["radio"] = when
                if corroboration:
                    text = corroboration.lstrip("✓ ").split(" · ")[0].split(" +")[0]
                    event["official"] = event["official"] or text
                    if "radio " in corroboration and " min earlier" in corroboration:
                        try:
                            minutes = int(corroboration.split("radio ", 1)[1].split(" min", 1)[0])
                            event["lead"] = max(event["lead"] or 0, minutes)
                        except ValueError:
                            pass
            if status and status not in event["statuses"]:
                event["statuses"].append(status)
            if lat is not None and lon is not None:
                event["points"].append((lat, lon))
        summaries = []
        for event_id, event in events.items():
            if regions and event["region"] not in regions:
                continue
            lead = event["lead"]
            if event["radio"] is not None and event["official_time"] is not None and event["radio"] < event["official_time"]:
                lead = max(lead or 0, int((event["official_time"] - event["radio"]).total_seconds() // 60))
            summaries.append(EventSummary(
                event_id, event["title"], event["category"], event["state"], event["region"], event["first"],
                event["last"], event["reports"], tuple(event["sources"]), event["urgency"], event["official"], lead,
                tuple(event["statuses"]), event["points"], event["closed_at"], event["close_reason"],
                event["closed_by"], event["exercise"], len(event["voices"])))
        return summaries

    def sender_track_records(self) -> dict:
        """Each radio station's track record (s2_watch.senders), by call sign. Exercise and test
        traffic doesn't count."""
        from s2_watch.senders import TrackRecord, callsign

        placeholders = ",".join("?" * len(OFFICIAL_SENDERS))
        confirmed_events = {row[0] for row in self.connection.execute(
            f"""SELECT DISTINCT event_id FROM reports WHERE event_id IS NOT NULL AND newsworthy = 1
                AND first_source = 'OSINT' AND (sender IN ({placeholders}) OR official = 1)""", sorted(OFFICIAL_SENDERS))}
        rows = self.connection.execute(
            """SELECT r.first_source, r.sender, r.status, r.corroboration, r.first_seen, r.event_id,
                      CASE WHEN r.sender = '' THEN m.raw_content ELSE '' END
               FROM reports r JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               WHERE r.newsworthy = 1 AND r.first_source != 'OSINT' AND r.exercise = ''""").fetchall()
        records: dict[str, TrackRecord] = {}
        for source, sender, status, corroboration, first_seen, event_id, text in rows:
            station = callsign(source, sender, text)
            if not station:
                continue
            confirmed = bool(corroboration) or (event_id is not None and event_id in confirmed_events)
            records.setdefault(station, TrackRecord(station)).add(datetime.fromisoformat(first_seen), confirmed, status)
        return records

    def event_of_report(self, report_id: int) -> int | None:
        row = self.connection.execute("SELECT event_id FROM reports WHERE id = ?", (report_id,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def report_first_message(self, report_id: int) -> tuple[Message, AnalysisResult | None, str] | None:
        """A report's first copy, its analysis and its official confirmation text."""
        row = self.connection.execute(
            """SELECT m.source_application, m.raw_content, m.received_at, m.fields_json, m.form_name,
                      a.newsworthy, a.category, a.urgency, a.summary, a.confidence, r.corroboration
               FROM reports r
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               LEFT JOIN analysis_results a ON a.message_id = m.id
               WHERE r.id = ?""", (report_id,),
        ).fetchone()
        if row is None:
            return None
        application, raw, received, fields_json, form, newsworthy, category, urgency, summary, confidence, official = row
        try:
            fields = json.loads(fields_json) if fields_json else {}
        except ValueError:
            fields = {}
        when = datetime.fromisoformat(received)
        message = Message(application, raw, received_at=when if when.tzinfo else when.replace(tzinfo=timezone.utc),
                          form_name=form, fields={str(k): str(v) for k, v in fields.items()})
        result = (AnalysisResult(bool(newsworthy), category, urgency, summary, float(confidence))
                  if newsworthy is not None else None)
        return message, result, official or ""

    def event_reports(self, event_id: int) -> list[dict]:
        """An event's reports in time order (its timeline)."""
        rows = self.connection.execute(
            """SELECT r.id, r.first_seen, r.last_seen, r.copies, r.sources, r.first_source, r.sender, r.urgency,
                      r.corroboration, r.status, r.lat, r.lon, COALESCE(a.summary, ''), m.raw_content, r.status_note,
                      r.exercise, r.state, r.region, r.place_level, r.event_time, r.official, m.fields_json
               FROM reports r
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               LEFT JOIN analysis_results a ON a.message_id = m.id
               WHERE r.event_id = ? AND r.newsworthy = 1 ORDER BY r.first_seen""",
            (event_id,),
        ).fetchall()
        keys = ("report_id", "first_seen", "last_seen", "copies", "sources", "source", "sender", "urgency",
                "corroboration", "status", "lat", "lon", "summary", "text", "status_note", "exercise", "state",
                "region", "place_level", "event_time", "official")
        reports = []
        for row in rows:
            report = dict(zip(keys, row[:-1]))
            try:
                report["fields"] = json.loads(row[-1]) if row[-1] else {}
            except ValueError:
                report["fields"] = {}
            reports.append(report)
        return reports

    def report_times(self, report_id: int) -> tuple[datetime | None, datetime | None]:
        """When a report was first and last heard (UTC)."""
        row = self.connection.execute("SELECT first_seen, last_seen FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return None, None
        return tuple(datetime.fromisoformat(value) if value else None for value in row)

    def report_status(self, report_id: int) -> tuple[str, str]:
        row = self.connection.execute(
            "SELECT status, status_note FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        return (row[0], row[1]) if row else ("", "")

    def set_report_status(self, report_ids, status: str, note: str | None = None) -> None:
        """Label reports; ``note`` None keeps each report's existing note."""
        if status not in REPORT_STATUSES:
            raise ValueError(f"Unknown report status {status!r}")
        changed = utc_iso(datetime.now(timezone.utc))
        for report_id in report_ids:
            if note is None:
                self.connection.execute(
                    "UPDATE reports SET status = ?, status_changed = ? WHERE id = ?", (status, changed, report_id))
            else:
                self.connection.execute(
                    "UPDATE reports SET status = ?, status_note = ?, status_changed = ? WHERE id = ?",
                    (status, note, changed, report_id))
        self.connection.commit()

    def ignored_reports(self, since: str, regions=None) -> int:
        clause, values = self._region_filter(regions)
        row = self.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE status = 'ignored' AND newsworthy = 1 AND first_seen >= ?" + clause,
            (since, *values),
        ).fetchone()
        return int(row[0])

    def save_analysis(self, message_id: int, result: AnalysisResult, analyzer: str = "") -> None:
        self.connection.execute(
            """INSERT INTO analysis_results(
                 message_id, newsworthy, category, urgency, summary, confidence, matched_terms, analyzer
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                 newsworthy = excluded.newsworthy,
                 category = excluded.category,
                 urgency = excluded.urgency,
                 summary = excluded.summary,
                 confidence = excluded.confidence,
                 matched_terms = excluded.matched_terms,
                 analyzer = excluded.analyzer""",
            (
                message_id,
                int(result.newsworthy),
                result.category,
                result.urgency,
                result.summary,
                result.confidence,
                "\x1f".join(result.matched_terms),
                analyzer,
            ),
        )
        self.connection.commit()

    def find_message_id(self, source_key: str, legacy_key: str | None = None) -> int | None:
        """The saved message with this key (or the key an earlier version used), if any."""
        for key in (source_key, legacy_key):
            if key:
                row = self.connection.execute("SELECT id FROM messages WHERE source_key = ?", (key,)).fetchone()
                if row:
                    return int(row[0])
        return None

    def saved_analysis(self, message_id: int, analyzer: str) -> AnalysisResult | None:
        """The analysis this analyzer already made of a message, so it needn't run again."""
        row = self.connection.execute(
            """SELECT newsworthy, category, urgency, summary, confidence, matched_terms FROM analysis_results
               WHERE message_id = ? AND analyzer = ?""", (message_id, analyzer),
        ).fetchone()
        if not row:
            return None
        newsworthy, category, urgency, summary, confidence, terms = row
        return AnalysisResult(bool(newsworthy), category, urgency, summary, float(confidence),
                              tuple(term for term in (terms or "").split("\x1f") if term))

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
