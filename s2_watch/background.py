"""The BACKGROUND section of MAGNET S2 reports and briefings, from what S2 Watch knows.

Facts only, no analysis: the official alerts standing at the place, earlier related events,
recent FEMA declarations, ongoing situations, space weather and the MagCon status. What only
the analyst knows (what is normal for the area or hazard) stays a short prompt after them.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from s2_watch.corroboration import FEMA_HAZARDS, _covers, _parse_time, hazards as hazards_in
from s2_watch.locating import load_gazetteer

# Alerts for boaters and beaches: many, and not what a briefing's standing conditions are about.
MARINE = ("small craft", "gale", "marine", "seas", "rip current", "beach hazard", "high surf", "brisk wind",
          "freezing spray", "storm warning", "hurricane force wind", "low water", "lake wind")
# Hazards the weather service warns about: only for these does a report's background mention NWS.
NWS_HAZARDS = {"flood", "heat", "fire", "winter", "wind", "tornado", "thunderstorm", "tropical", "fog", "air", "coastal",
               "avalanche"}
EVENT_PROMPT = "(Add what is normal for this area or hazard.)"
PERIOD_PROMPT = "(Add the basis for the MagCon status and anything else the reader needs as context.)"


def _day(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return f"{value.astimezone(timezone.utc):%d %b}"


def _until(alert: dict) -> str:
    ends = _parse_time(alert.get("ends") or "")
    return f" (until {ends.astimezone(timezone.utc):%d %b %H%MZ})" if ends else ""


def alerts_at(alerts: list[dict], lat: float, lon: float, now: datetime) -> list[dict]:
    """The NWS alerts active now that cover a point (inside the outline or its county)."""
    county = load_gazetteer().county_at(lat, lon)
    active = []
    for alert in alerts:
        ends = _parse_time(alert.get("ends") or "")
        if (ends is None or ends > now) and _covers(alert, lat, lon, county):
            active.append(alert)
    return active


def _declarations(messages, state: str, now: datetime, hazards: set[str] | None = None) -> list[str]:
    """FEMA declarations for a state (or all, with "") in the last 30 days, newest first; with
    ``hazards``, only those declared for one of them."""
    found = []
    for message in sorted(messages, key=lambda m: m.received_at, reverse=True):
        if message.fields.get("type") != "FEMA DECLARATION" or (state and message.fields.get("state") != state):
            continue
        if hazards is not None:
            incident = message.raw_content.split("incident type", 1)[-1].split(";", 1)[0].strip().lower()
            if not FEMA_HAZARDS.get(incident, set()) & hazards:
                continue
        received = message.received_at if message.received_at.tzinfo else message.received_at.replace(tzinfo=timezone.utc)
        if now - received <= timedelta(days=30):
            words = message.raw_content.split(":", 1)[-1].strip()
            found.append(f"{words} ({_day(received)})")
    return found


def event_background(event, related: list[dict], alerts: list[dict], official_messages, now: datetime,
                     hazards: set[str] | None = None) -> list[str]:
    """For a follow-up report on one event (an EventSummary) about ``hazards`` (flood, fire...).
    Only sources related to the report are mentioned: NWS alerts and FEMA declarations for the
    same kind of hazard, and nothing from them for a report that isn't about one."""
    hazards = set(hazards or ())
    weather = hazards & NWS_HAZARDS
    lines = []
    point = event.points[0] if event.points else None
    gazetteer = load_gazetteer()
    state = event.state if event.state in gazetteer.state_names else ""
    us = bool(state)
    if not state and point is not None:       # a point in the U.S. with no state attached
        from s2_watch.world import load_world

        us = load_world().area_of(*point) == "us"
        state = (gazetteer.locate_point(*point)[0] or "") if us else ""
    if point and us and weather:
        standing = [alert for alert in alerts_at(alerts, point[0], point[1], now)
                    if hazards_in(alert.get("event", "")) & weather]
        if standing:
            names = "; ".join(f"{alert['event']}{_until(alert)}" for alert in standing[:4])
            more = f" and {len(standing) - 4} more" if len(standing) > 4 else ""
            lines.append(f"NWS watches and warnings for this hazard in effect at the location: {names}{more}.")
        else:
            lines.append("No NWS watches or warnings for this hazard are in effect at the location as of this DTG.")
    if related:
        # Events with one title (several "Flood — Florida" the same day) read as one line with a count.
        grouped: dict[str, list[dict]] = {}
        for other in related:
            grouped.setdefault(other["title"], []).append(other)
        items = []
        for title, group in list(grouped.items())[:4]:
            reports = sum(other["reports"] for other in group)
            official = next((other["official"] for other in group if other["official"]), "")
            closed = [other for other in group if other["closed"]]
            outcome = ("closed: " + closed[0]["close_reason"] if len(closed) == len(group) and closed[0]["close_reason"]
                       else "closed" if len(closed) == len(group) else "")
            days = sorted({_day(other["first_seen"]) for other in group})
            when = days[0] if len(days) == 1 else f"{days[0]}–{days[-1]}"
            count = f" ×{len(group)}" if len(group) > 1 else ""
            details = ", ".join(part for part in (f"{reports} report{'s' if reports != 1 else ''}",
                                                  f"confirmed by {official}" if official else "", outcome) if part)
            items.append(f"{when}: {title}{count} ({details})")
        more = f"; {len(grouped) - 4} more" if len(grouped) > 4 else ""
        lines.append(f"Earlier related activity in the previous 30 days: {'; '.join(items)}{more}.")
    else:
        lines.append("S2 Watch recorded no similar events within 150 km or in the same state in the previous 30 days.")
    if state and hazards:
        declarations = _declarations(official_messages, state, now, hazards)
        if declarations:
            lines.append(f"FEMA declarations for {gazetteer.state_names.get(state, state)} in the last 30 days: "
                         f"{'; '.join(declarations[:3])}.")
    elif not us and event.state:
        lines.append(f"Location: {event.state}, outside the United States.")
    return lines + [EVENT_PROMPT]


def period_background(events, alerts: list[dict], official_messages, now: datetime, start: datetime, magcon: str,
                      coverage: str = "") -> list[str]:
    """For a shift briefing over [start, now]."""
    lines = []
    active = [alert for alert in alerts
              if (_parse_time(alert.get("ends") or "") or now + timedelta(seconds=1)) > now
              and not any(word in alert.get("event", "").lower() for word in MARINE)]
    if active:
        by_severity = Counter(alert.get("severity") or "Unknown" for alert in active)
        parts = []
        for severity in ("Extreme", "Severe", "Moderate", "Minor"):
            if by_severity.get(severity):
                kinds = Counter(alert["event"] for alert in active if alert.get("severity") == severity).most_common(3)
                parts.append(f"{by_severity[severity]} {severity.lower()} ("
                             + ", ".join(f"{name}{f' ×{count}' if count > 1 else ''}" for name, count in kinds) + ")")
        lines.append(f"Standing conditions: {len(active)} NWS watches and warnings (marine ones aside) are active "
                     f"across the U.S.: "
                     + "; ".join(parts) + ".")
    else:
        lines.append("Standing conditions: no NWS watches or warnings are active across the U.S. as of this DTG.")
    ongoing = [event for event in events if event.first_seen < start and event.closed_at is None]
    if ongoing:
        ongoing.sort(key=lambda e: (e.urgency != "high", -e.reports))
        items = [f"{event.title} (since {_day(event.first_seen)}, {event.reports} report{'s' if event.reports != 1 else ''})"
                 for event in ongoing[:5]]
        lines.append(f"Ongoing situations that began before this period: {'; '.join(items)}"
                     + (f"; {len(ongoing) - 5} more" if len(ongoing) > 5 else "") + ".")
    declarations = _declarations(official_messages, "", now)
    if declarations:
        lines.append(f"FEMA declarations in the last 30 days: {'; '.join(declarations[:4])}"
                     + (f"; {len(declarations) - 4} more" if len(declarations) > 4 else "") + ".")
    space = [m for m in official_messages if m.fields.get("type") == "SPACE WEATHER"
             and now - (m.received_at if m.received_at.tzinfo else m.received_at.replace(tzinfo=timezone.utc)) <= timedelta(hours=24)]
    if space:
        latest = max(space, key=lambda m: m.received_at)
        lines.append(f"{latest.raw_content} (NOAA SWPC, {_day(latest.received_at)}).")
    else:
        lines.append("Space weather: no NOAA radio blackout R2+, radiation storm S2+ or geomagnetic storm G3+ in the last 24 hours.")
    lines.append(f"MagCon status: {magcon}.")
    if coverage:
        lines.append(f"Coverage: {coverage}.")
    return lines + [PERIOD_PROMPT]
