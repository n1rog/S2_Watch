"""A research packet: everything S2 Watch knows about one event, to start research elsewhere.

The operator pastes it as the first message of a research conversation (Claude, or any
assistant). Its reports are numbered as in the event panel, so the research can cite the
network's own observations ([3]) beside the web sources it finds, and a reader can tell
the two apart. Radio reports are marked firsthand but unverified; official ones as such.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from s2_watch.analysis.ollama import readable_text
from s2_watch.models import Message

PROMPT = """HOW TO USE THIS PACKET
Research this event and write a MAGNET S2 report for distribution.
- Start from the numbered reports below. Radio reports are firsthand observations from amateur radio
  stations: timely but unverified. Official reports (NWS, USGS, FAA...) are authoritative. News reports
  are secondhand.
- Cite these reports by their number, e.g. [3], and cite web sources you find separately, with their links.
- Say where the reports and your sources agree, where they conflict, and what is still unknown.
- Use estimative language: very likely (80-95%), likely (55-80%), roughly even (45-55%),
  unlikely (20-45%), very unlikely (5-20%).
- Don't add facts that neither the reports nor a cited source give."""


def _utc(value) -> str:
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%d %b %Y %H:%MZ")


def _kind(report: dict) -> str:
    if report.get("official"):      # a flag: the agency is the sender (NWS, USGS, FAA...)
        return f"official ({report.get('sender') or 'official source'})"
    if report.get("source") == "OSINT":
        return "news"
    return "radio, firsthand, unverified"


def research_packet(event, reports: list[dict], *, concern=None, assessment=None, callsign: str = "",
                    version: str = "", now: datetime | None = None) -> str:
    """The packet as plain text. ``reports``: store.event_reports(event_id) (oldest first, as
    numbered in the event panel); ``concern``: its Concern; ``assessment``: the accepted one."""
    now = now or datetime.now(timezone.utc)
    area = ", ".join(part for part in (event.state, f"MARS Region {event.region}" if event.region else "") if part)
    lines = [f"S2 WATCH RESEARCH PACKET: {event.title}",
             f"Prepared {_utc(now)}" + (f" by {callsign.upper()}" if callsign else "")
             + (f" with S2 Watch {version}" if version else ""), ""]
    if getattr(event, "exercise", False):
        lines += ["*** EXERCISE EXERCISE EXERCISE: these reports are exercise traffic, not a real event ***", ""]
    lines += ["EVENT",
              f"Category: {event.category}" + (f" | Area: {area}" if area else "") + f" | Urgency: {event.urgency}",
              f"Heard: {_utc(event.first_seen)} to {_utc(event.last_seen)} | {event.reports} report"
              f"{'s' if event.reports != 1 else ''} from {', '.join(event.sources)}",
              f"Official confirmation: {event.official or 'none found'}"]
    if getattr(event, "radio_lead_minutes", None):
        lines.append(f"Radio reported it {event.radio_lead_minutes} minutes before the first official source")
    if concern is not None:
        lines.append(f"S2 Watch concern level: {concern.text()}")
    if event.closed_at is not None:
        lines.append(f"Closed {_utc(event.closed_at)}" + (f": {event.close_reason}" if event.close_reason else ""))
    if assessment is not None:
        who = assessment.accepted_by or "not yet accepted"
        lines += ["", f"ANALYST ASSESSMENT (accepted by {who}" + (f", {assessment.accepted_at}" if assessment.accepted_at
                                                                   else "") + ")",
                  f"BLUF: {assessment.bluf}",
                  f"Trend: {assessment.trend}" + (f": {assessment.trend_reason}" if assessment.trend_reason else "")]
        if assessment.agreement:
            lines.append(f"Agreement: {assessment.agreement}")
        lines += [f"Probability: {statement} | {level}" for statement, level in assessment.probabilities]
        lines += [f"Gap: {gap}" for gap in assessment.gaps]
    lines += ["", "REPORTS (cite as [n])"]
    for number, report in enumerate(reports, 1):
        sender = report.get("sender") or ""
        fields = report.get("fields") or {}
        who = " ".join(part for part in (report.get("source", ""), sender, fields.get("outlet", "")) if part)
        place = ", ".join(str(part) for part in (report.get("state"), f"R{report['region']}" if report.get("region")
                                                 else "") if part)
        grid = fields.get("grid", "")
        head = f"[{number}] {_utc(report['first_seen'])} | {who} | {_kind(report)}"
        if place or grid:
            head += " | " + ", ".join(part for part in (place, f"grid {grid}" if grid else "") if part)
        if report.get("exercise") == "exercise":
            head += " | EXERCISE"
        lines.append(head)
        body = " ".join(readable_text(Message(report.get("source", ""), report.get("text") or "")).split())
        lines.append(f"    {body}")
        headline = (report.get("summary") or "").strip()
        words = lambda text: {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2}
        if headline and not words(headline) <= words(body):      # only when it adds something
            lines.append(f"    Headline: {headline}")
        if report.get("corroboration"):
            lines.append(f"    Confirmation: {report['corroboration']}")
        if fields.get("url"):
            lines.append(f"    Link: {fields['url']}")
        if report.get("copies", 1) > 1:
            lines.append(f"    Received {report['copies']} times via {report.get('sources', '')}")
    lines += ["", PROMPT]
    return "\n".join(lines) + "\n"
