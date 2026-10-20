"""Shift briefings drafted from events: the full briefing first, then a JS8Call version.

The full briefing lists the priority items (focus regions first, then by urgency, with
radio-reported events ahead of official-only ones), activity by MARS region, quieter
categories, and what is new since the last briefing. Like follow-up reports, the radio
versions are made from the (possibly edited) full text.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.followup import JS8CALL_TARGET, JS8CALL_UNSAFE, ascii_upper, dtg

PRIORITY_ITEMS = 8
URGENCY_RANK = {"high": 3, "medium": 2, "low": 1}


def _area(event) -> str:
    # The title often names the place already ("Flooding — Tulsa, OK"): then only the region.
    from s2_watch.locating import load_gazetteer

    state_name = load_gazetteer().state_names.get(event.state or "", "")
    named = event.state and (f", {event.state}" in event.title or (state_name and state_name in event.title))
    parts = ("" if named else event.state, f"MARS R{event.region}" if event.region else "")
    return ", ".join(part for part in parts if part) or "location unknown"


def _is_ignored(event) -> bool:
    return bool(event.statuses) and set(event.statuses) == {"ignored"}


def priority_key(event, focus: set[int]):
    radio = any(source != "OSINT" for source in event.sources)
    from s2_watch.focus import event_in_focus

    return (event_in_focus(event, focus), URGENCY_RANK.get(event.urgency, 0), radio, event.reports,
            event.last_seen)


def briefing_report(events, now: datetime, hours: int, callsign: str = "", focus: set[int] | None = None,
                    last_briefing: datetime | None = None) -> str:
    focus = set(focus or ())
    events = [event for event in events if not _is_ignored(event)]
    start = now - timedelta(hours=hours)
    ranked = sorted(events, key=lambda event: priority_key(event, focus), reverse=True)
    # Priority items are U.S. events with a known place, or anything the radio network reported.
    priority = [event for event in ranked if event.urgency in ("high", "medium")
                and (event.region or any(source != "OSINT" for source in event.sources))][:PRIORITY_ITEMS]
    counts = Counter(event.urgency for event in events)
    confirmed = sum(bool(event.official) for event in events)
    radio_first = sum(bool(event.radio_lead_minutes) for event in events)
    new = [event for event in events if last_briefing and event.first_seen > last_briefing]
    lines = [
        "S2 WATCH BRIEFING",
        f"DTG: {dtg(now)}",
        f"FROM: {callsign or '(your callsign)'}",
        f"PERIOD: {dtg(start)} TO {dtg(now)} ({hours} HOURS)",
        "FOCUS: " + (", ".join(f"MARS REGION {region}" for region in sorted(focus)) if focus else "WHOLE U.S."),
        f"SUMMARY: {len(events)} events ({counts['high']} high, {counts['medium']} medium urgency); "
        f"{confirmed} confirmed by official sources; radio reported {radio_first} before official sources."
        + (f" {len(new)} new since the last briefing." if last_briefing else ""),
        "",
        "PRIORITY ITEMS:",
    ]
    if not priority:
        lines.append("None.")
    for number, event in enumerate(priority, 1):
        sources = ", ".join(event.sources)
        details = [f"{event.reports} report{'s' if event.reports != 1 else ''} ({sources})",
                   f"Official: {event.official}" if event.official else "Not yet confirmed by official sources"]
        if event.radio_lead_minutes:
            details.append(f"radio {event.radio_lead_minutes} min ahead")
        flag = " [NEW]" if event in new else ""
        lines.append(f"{number}. [{event.urgency.upper()}] {event.title} — {_area(event)}. {'; '.join(details)}.{flag}")
    by_region: dict[int, list] = defaultdict(list)
    for event in ranked:
        if event.region:
            by_region[event.region].append(event)
    lines += ["", "BY MARS REGION:"]
    if not by_region:
        lines.append("No events with a known region.")
    for region in sorted(by_region, key=lambda r: (r not in focus, r)):
        items = by_region[region]
        top = "; ".join(event.title for event in items[:3])
        lines.append(f"R{region}{' (focus)' if region in focus else ''}: {len(items)} event{'s' if len(items) != 1 else ''}"
                     f" - {top}{' ...' if len(items) > 3 else ''}")
    by_category = Counter(event.category for event in events)
    quiet = [category for category in NEWS_CATEGORIES if not by_category.get(category)]
    lines += ["", "ACTIVITY BY CATEGORY: " + ("; ".join(f"{category} {count}" for category, count in by_category.most_common())
                                          or "none"),
              "QUIET: " + (", ".join(quiet) if quiet else "none"),
              "", "ASSESSMENT:", "(add your assessment: overall situation, key concerns, what to watch next)"]
    return "\n".join(lines)


# "1. [HIGH] <title, may contain —> — <area>. 3 reports ..." (the area follows the last " — ")
PRIORITY_LINE = re.compile(r"^\d+\. \[(\w+)\] (.+) — ([^—]*?)\. \d+ reports?")


def js8_briefing(report: str) -> str:
    """A short JS8Call message from the full briefing: date-time, count, top items."""
    fields = {}
    items = []
    for line in report.splitlines():
        match = re.match(r"\s*([A-Z][A-Z ]+):\s*(\S.*)$", line)
        if match and match.group(1) not in fields:
            fields[match.group(1)] = match.group(2)
        item = PRIORITY_LINE.match(line.strip())
        if item:
            urgency, title, area = item.groups()
            title = title.split(" · ")[0]                     # headline without its details
            first = area.split(",")[0].strip()
            state = first if len(first) == 2 else ""          # "OK, MARS R6" -> OK
            items.append(f"{urgency} {title.replace(' — ', ' ')}" + (f" {state}" if state and " — " not in title else ""))
    summary = fields.get("SUMMARY", "")
    count = summary.split(" events", 1)[0] if " events" in summary else ""
    head = f"S2 BRIEF {fields.get('DTG', '')}" + (f" {count} EVENTS" if count else "")
    callsign = fields.get("FROM", "")
    tail = f" DE {callsign}" if callsign and not callsign.startswith("(") else ""
    text = head
    for number, item in enumerate(items, 1):
        piece = f" {number}) {item}"
        if len(ascii_upper(text + piece + tail)) > JS8CALL_TARGET:
            break
        text += piece
    return JS8CALL_UNSAFE.sub("", ascii_upper(text + tail)).strip()
