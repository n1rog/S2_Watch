import json
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
        for column in ("status", "status_note", "status_changed", "corroboration"):
            if column not in report_columns:
                self.connection.execute(f"ALTER TABLE reports ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        self.connection.execute("CREATE INDEX IF NOT EXISTS reports_first_seen ON reports(first_seen)")
        # Where a report is (its most specific point) and the event it belongs to.
        for column, kind in (("lat", "REAL"), ("lon", "REAL"), ("place_level", "TEXT NOT NULL DEFAULT ''"),
                             ("event_id", "INTEGER")):
            if column not in report_columns:
                self.connection.execute(f"ALTER TABLE reports ADD COLUMN {column} {kind}")
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
        self.connection.execute("CREATE INDEX IF NOT EXISTS reports_event_id ON reports(event_id)")
        message_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(messages)")}
        if "source_key" not in message_columns:
            self.connection.execute("ALTER TABLE messages ADD COLUMN source_key TEXT")
        if "report_id" not in message_columns:
            self.connection.execute("ALTER TABLE messages ADD COLUMN report_id INTEGER")
        if "fields_json" not in message_columns:
            # The source's structured fields (grid, sender, coordinates...), so a report
            # can be shown and mapped again at launch without re-reading its source.
            self.connection.execute("ALTER TABLE messages ADD COLUMN fields_json TEXT NOT NULL DEFAULT ''")
        self.connection.execute("CREATE INDEX IF NOT EXISTS messages_report_id ON messages(report_id)")
        analysis_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(analysis_results)")}
        if "analyzer" not in analysis_columns:
            # Which analyzer produced the result ("rules", "ollama:<model>"), so a message
            # read again isn't analysed again by the same one.
            self.connection.execute("ALTER TABLE analysis_results ADD COLUMN analyzer TEXT NOT NULL DEFAULT ''")
        self._remove_repeated_messages()
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS messages_source_key ON messages(source_key)"
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(watch_forms)")}
        for column in ("message_type", "sender"):
            if column not in columns:
                self.connection.execute(
                    f"ALTER TABLE watch_forms ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        self.connection.commit()

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
        row = self.connection.execute(
            "SELECT report_id FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row and row[0] is not None:
            report_id = int(row[0])
            if result is not None:
                # The report follows the latest analysis of its first copy (a later, better
                # analyzer such as Ollama can find a rules match was routine chat).
                self.connection.execute(
                    """UPDATE reports SET newsworthy = ?, category = ?, urgency = ?
                       WHERE id = ? AND (SELECT MIN(id) FROM messages WHERE report_id = ?) = ?""",
                    (int(result.newsworthy), result.category, result.urgency, report_id, report_id, message_id))
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
                     sender, newsworthy, category, urgency, state, region)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (text, utc_iso(seen), utc_iso(seen), message.source_application, message.source_application,
                 sender, int(newsworthy), category, urgency, state, region),
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

    # -- possible misses ---------------------------------------------------------------

    def missed_checked(self, key: str) -> bool:
        return self.connection.execute("SELECT 1 FROM missed_checks WHERE key = ?", (key,)).fetchone() is not None

    def save_missed_check(self, key: str, message: Message, result: AnalysisResult, status: str) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO missed_checks(key, checked_at, source_application, raw_content, received_at,
                 fields_json, newsworthy, category, urgency, summary, confidence, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, utc_iso(datetime.now(timezone.utc)), message.source_application, message.raw_content,
             message.received_at.isoformat(), json.dumps(message.fields) if message.fields else "",
             int(result.newsworthy), result.category, result.urgency, result.summary, result.confidence, status),
        )
        self.connection.commit()

    def possible_misses(self, status: str = "new") -> list[tuple[str, Message, AnalysisResult]]:
        """Messages the model thinks are news though no watch form kept them, newest first."""
        rows = self.connection.execute(
            """SELECT key, source_application, raw_content, received_at, fields_json, newsworthy, category, urgency,
                      summary, confidence FROM missed_checks WHERE status = ? ORDER BY received_at DESC""",
            (status,),
        ).fetchall()
        misses = []
        for key, application, raw, received, fields_json, newsworthy, category, urgency, summary, confidence in rows:
            try:
                fields = json.loads(fields_json) if fields_json else {}
            except ValueError:
                fields = {}
            when = datetime.fromisoformat(received)
            message = Message(application, raw, received_at=when if when.tzinfo else when.replace(tzinfo=timezone.utc),
                              fields={str(k): str(v) for k, v in fields.items()})
            misses.append((key, message, AnalysisResult(bool(newsworthy), category, urgency, summary, float(confidence))))
        return misses

    def set_missed_status(self, key: str, status: str) -> None:
        self.connection.execute("UPDATE missed_checks SET status = ? WHERE key = ?", (status, key))
        self.connection.commit()

    def missed_counts(self) -> dict[str, int]:
        return dict(self.connection.execute("SELECT status, COUNT(*) FROM missed_checks GROUP BY status").fetchall())

    # -- events ----------------------------------------------------------------------

    def assign_event(self, report_id: int, category: str, hazards: set[str], place, title: str,
                     incident: str = "", terms: set[str] | None = None) -> int:
        """Put a newsworthy report in its event (an existing one, or a new one); the event id."""
        from s2_watch.events import EVENT_GAP, INCIDENT_GAP, EventCandidate, Place, choose_event, clean_title

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
        self.connection.commit()
        return int(event_id)

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
                      r.sender
               FROM events e JOIN reports r ON r.event_id = e.id
               WHERE e.last_seen >= ? AND r.newsworthy = 1
               ORDER BY e.last_seen DESC, r.first_seen""",
            (utc_iso(since),),
        ).fetchall()
        rank = {"low": 1, "medium": 2, "high": 3}
        events: dict[int, dict] = {}
        for (event_id, title, category, state, region, first, last, seen, sources, first_source, urgency,
             corroboration, status, lat, lon, sender) in rows:
            event = events.setdefault(event_id, {
                "title": title, "category": category, "state": state, "region": region,
                "first": datetime.fromisoformat(first), "last": datetime.fromisoformat(last),
                "reports": 0, "sources": [], "urgency": "low", "official": "", "radio": None,
                "official_time": None, "lead": None, "statuses": [], "points": []})
            event["reports"] += 1
            for source in sources.split(","):
                if source and source not in event["sources"]:
                    event["sources"].append(source)
            if rank.get(urgency, 0) > rank.get(event["urgency"], 0):
                event["urgency"] = urgency
            when = datetime.fromisoformat(seen)
            if first_source == "OSINT" and sender in OFFICIAL_SENDERS:
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
                tuple(event["statuses"]), event["points"]))
        return summaries

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
                      r.corroboration, r.status, r.lat, r.lon, COALESCE(a.summary, ''), m.raw_content, m.fields_json
               FROM reports r
               JOIN messages m ON m.id = (SELECT MIN(id) FROM messages WHERE report_id = r.id)
               LEFT JOIN analysis_results a ON a.message_id = m.id
               WHERE r.event_id = ? AND r.newsworthy = 1 ORDER BY r.first_seen""",
            (event_id,),
        ).fetchall()
        keys = ("report_id", "first_seen", "last_seen", "copies", "sources", "source", "sender", "urgency",
                "corroboration", "status", "lat", "lon", "summary", "text")
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
