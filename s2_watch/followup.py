"""Drafts for a follow-up report: a full report first, then FLMSG and JS8Call versions of it.

This follows how follow-ups are written: the full report is drafted (and edited) first,
and the radio messages are made from the full report, not from the original message.
The FLMSG and JS8Call texts are therefore built by reading the full report's text, so the
operator's edits carry through. Nothing is sent: the texts are copied or saved.
"""

from __future__ import annotations

import re
import textwrap
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from s2_watch.analysis.base import AnalysisResult
from s2_watch.models import Message

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
FLMSG_WIDTH = 69
JS8CALL_TARGET = 200          # characters: long enough for the essentials, short on the air
# Characters that go out cleanly as JS8Call free text.
JS8CALL_UNSAFE = re.compile(r"[^A-Z0-9 .,/?+\-@:()']")


@dataclass
class FollowUpFacts:
    message: Message
    result: AnalysisResult
    locations: list            # ResolvedLocation, most specific first
    callsign: str = ""
    region: int | None = None
    state: str = ""
    copies: int = 1
    sources: tuple[str, ...] = ()
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    official: str = ""         # the Official column's confirmation, if any
    note: str = ""             # the operator's label note
    now: datetime | None = None


def dtg(when: datetime) -> str:
    """Date-time group, e.g. 291452Z SEP 26."""
    when = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return f"{when:%d%H%M}Z {MONTHS[when.month - 1]} {when:%y}"


def ascii_upper(text: str) -> str:
    text = text.replace("—", " - ").replace("–", "-").replace("✓", "").replace("·", "-").replace("×", "X")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[ \t]+", " ", text).upper().strip()


def full_report(facts: FollowUpFacts) -> str:
    """The full follow-up report, in labelled lines the operator can edit."""
    now = facts.now or datetime.now(timezone.utc)
    # A position read from the report's coordinates has a placeholder label; say where.
    label = lambda location: (f"{location.lat:.3f}, {location.lon:.3f}" if location.label.startswith("Coordinates")
                              else location.label)
    places = [label(location) for location in facts.locations if getattr(location, "kind", "news") != "region"]
    general = [location.label for location in facts.locations if getattr(location, "kind", "") == "region"]
    location = "; ".join(places) or "; ".join(general) or "Unknown"
    first = facts.locations[0] if facts.locations else None
    area = ", ".join(part for part in (
        facts.state, f"MARS Region {facts.region}" if facts.region else "") if part)
    lines = [
        "S2 WATCH FOLLOW-UP REPORT",
        f"DTG: {dtg(now)}",
        f"FROM: {facts.callsign or '(your callsign)'}",
        f"SUBJECT: {facts.result.category} - {facts.result.summary.replace('Coordinates in report', location)}",
        f"PRIORITY: {facts.result.urgency.upper()}",
        f"LOCATION: {location}",
    ]
    if first is not None and getattr(first, "kind", "news") != "region":
        lines.append(f"POSITION: {first.lat:.3f}, {first.lon:.3f}")
    if area:
        lines.append(f"AREA: {area}")
    if facts.first_seen:
        seen = f"FIRST HEARD: {dtg(facts.first_seen)}"
        if facts.last_seen and facts.last_seen != facts.first_seen:
            seen += f" / LAST HEARD: {dtg(facts.last_seen)}"
        lines.append(seen)
    sender = facts.message.fields.get("sender") or facts.message.fields.get("from") or ""
    lines.append(f"SOURCE: {facts.message.source_application}" + (f" ({sender})" if sender else "")
                 + (f", {facts.copies} copies via {', '.join(facts.sources)}" if facts.copies > 1 else ""))
    lines.append(f"OFFICIAL CONFIRMATION: {facts.official.lstrip('✓ ').strip() or 'None found'}")
    lines += ["", "ORIGINAL MESSAGE:", facts.message.raw_content.strip(), "",
              "ASSESSMENT:", facts.note or "(add your assessment and actions taken)"]
    return "\n".join(lines)


def report_fields(report: str) -> dict[str, str]:
    """The labelled one-line fields of a (possibly edited) full report."""
    fields = {}
    for line in report.splitlines():
        match = re.match(r"\s*([A-Z][A-Z ]+):\s*(\S.*)$", line)
        if match and match.group(1) not in fields:
            fields[match.group(1).strip()] = match.group(2).strip()
    return fields


def flmsg_text(report: str) -> str:
    """The full report as an FLMSG message body: plain upper-case ASCII, wrapped for radio."""
    lines = []
    for line in ascii_upper_lines(report):
        lines.extend(textwrap.wrap(line, FLMSG_WIDTH) or [""])
    return "\n".join(lines).strip() + "\n"


def ascii_upper_lines(report: str) -> list[str]:
    return [ascii_upper(line) for line in report.splitlines()]


def js8call_text(report: str) -> str:
    """A short JS8Call message made from the full report's fields."""
    fields = {key: ascii_upper(value) for key, value in report_fields(report).items()}
    subject = fields.get("SUBJECT", "")
    parts = ["S2 FOLLOWUP", fields.get("DTG", "")]
    if fields.get("PRIORITY") and fields["PRIORITY"] not in ("LOW", "MEDIUM"):
        parts.append(fields["PRIORITY"])
    location = fields.get("LOCATION", "")
    tail = [f"AT {location}" if location and location != "UNKNOWN" else ""]
    official = fields.get("OFFICIAL CONFIRMATION", "")
    if official and official != "NONE FOUND":
        tail.append(f"CONFIRMED {official}")
    callsign = fields.get("FROM", "")
    if callsign and not callsign.startswith("("):
        tail.append(f"DE {callsign}")
    fixed = " ".join(part for part in parts + tail if part)
    room = max(20, JS8CALL_TARGET - len(fixed) - 1)
    if len(subject) > room:
        subject = subject[:room - 3].rstrip(" -,") + "..."
    text = " ".join(part for part in parts + [subject] + tail if part)
    return JS8CALL_UNSAFE.sub("", text).strip()
