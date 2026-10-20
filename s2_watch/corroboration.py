"""Match radio reports to official sources describing the same hazard at the same place.

Sources: active NWS alerts (inside the alert's outline or one of its counties), USGS
earthquakes, SPC storm reports, NIFC wildfires, FEMA declarations (same state) and FAA
security flight restrictions. Each must be about the same kind of hazard as the report,
near its location and close in time. Report locations must be specific (coordinates,
grid, city, or county), except that a FEMA declaration can confirm any report placed in
its state. When the radio report came before the official one, that lead time is
noted: early reporting is the point of an S2 network.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from s2_watch.locating import _point_in_polygon, load_gazetteer

# Hazard families, recognised in report text and in NWS event names. Words match whole
# words (plurals allowed); a trailing "*" matches any word starting so ("snow*": snowfall),
# so "shot" isn't heat and "police" isn't ice.
HAZARDS = {
    "flood": ("flood*", "flash flood*", "high water", "rising water"),
    "heat": ("heat*", "triple digit*", "hot"),
    "fire": ("fire", "wildfire*", "brushfire*", "grassfire*", "forest fire*", "red flag", "smoke*", "burn*"),
    "winter": ("winter*", "snow*", "blizzard*", "ice", "icy", "freez*", "frost*", "cold", "chill*", "sleet*"),
    "wind": ("wind", "windy", "gust*", "dust*", "derecho*"),
    "tornado": ("tornado*", "funnel*"),
    "thunderstorm": ("thunderstorm*", "tstorm*", "t-storm*", "hail", "hailstone*", "lightning", "severe storm*",
                     "severe weather"),
    "tropical": ("hurricane*", "tropical*", "storm surge*", "typhoon*"),
    "fog": ("fog", "foggy", "visibility"),
    "air": ("air quality", "smoke*", "aqi"),
    "coastal": ("surf", "rip current*", "coastal*", "tsunami*"),
    "earthquake": ("earthquake*", "quake*"),
    "avalanche": ("avalanche*",),
    "security": ("security", "vip", "motorcade*", "secret service", "presidential", "president", "flight restriction*",
                 "tfr", "no-fly", "no fly"),
}
_HAZARD_PATTERNS = {
    family: re.compile("|".join(
        r"\b" + re.escape(word[:-1]) if word.endswith("*") else r"\b" + re.escape(word) + r"(?:s|es)?\b"
        for word in words))
    for family, words in HAZARDS.items()
}
FEMA_HAZARDS = {
    "fire": {"fire", "air"}, "flood": {"flood"}, "severe storm": {"thunderstorm", "wind", "flood", "tornado"},
    "hurricane": {"tropical", "flood", "wind"}, "tropical storm": {"tropical", "flood", "wind"},
    "typhoon": {"tropical", "flood", "wind"}, "tornado": {"tornado", "thunderstorm"}, "winter storm": {"winter"},
    "severe ice storm": {"winter"}, "snowstorm": {"winter"}, "earthquake": {"earthquake"},
    "coastal storm": {"coastal", "flood", "wind"}, "mud/landslide": {"flood"},
}
SPECIFIC_LEVELS = {"precise", "city", "county", "grid"}


@dataclass(frozen=True)
class Corroboration:
    event: str                 # e.g. "Flood Watch", "M4.1 quake"
    severity: str
    sent: str
    lead_minutes: int | None   # how long before the official source the radio report came in
    source: str = "NWS"
    others: int = 0            # further official items confirming the same report

    def text(self) -> str:
        lead = f" · radio {self.lead_minutes} min earlier" if self.lead_minutes and self.lead_minutes > 0 else ""
        more = f" +{self.others}" if self.others else ""
        return f"✓ {self.source} {self.event}{lead}{more}"


@dataclass
class OfficialItem:
    """A point-like official observation (not an NWS alert area)."""

    source: str
    event: str
    hazards: set[str]
    time: datetime
    lat: float | None = None
    lon: float | None = None
    radius_km: float = 50
    window: timedelta = timedelta(hours=24)
    state: str = ""            # FEMA: matched by state instead of distance
    rank: int = 1


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(x, math.radians(lat2 - lat1)) * 6371


def official_items(messages, resolver=None) -> list[OfficialItem]:
    """Official items from the OSINT feed (USGS, SPC, NIFC, FEMA, FAA) for corroboration."""
    items = []
    for message in messages:
        fields, kind = message.fields, message.fields.get("type", "")
        when = message.received_at if message.received_at.tzinfo else message.received_at.replace(tzinfo=timezone.utc)
        try:
            lat, lon = float(fields["lat"]), float(fields["lon"])
        except (KeyError, ValueError):
            lat = lon = None
        if kind == "EARTHQUAKE" and lat is not None:
            items.append(OfficialItem("USGS", f"M{fields.get('magnitude', '?')} quake", {"earthquake"}, when, lat, lon,
                                      radius_km=150, rank=3))
        elif kind == "SPC STORM REPORT" and lat is not None:
            event = message.raw_content.split(":", 1)[1].split("—", 1)[0].strip().split(" ")[0].lower()
            hazards = {"tornado": {"tornado", "thunderstorm"}, "hail": {"thunderstorm"}}.get(event, {"wind", "thunderstorm"})
            items.append(OfficialItem("SPC", f"{event} report", hazards, when, lat, lon, radius_km=50,
                                      window=timedelta(hours=12), rank=3))
        elif kind == "WILDFIRE" and lat is not None:
            name = message.raw_content.split(":", 1)[1].split("—", 1)[0].strip()
            items.append(OfficialItem("NIFC", name, {"fire", "air"}, when, lat, lon, radius_km=60,
                                      window=timedelta(days=30), rank=2))
        elif kind == "FEMA DECLARATION" and fields.get("state"):
            incident = message.raw_content.split("incident type", 1)[-1].split(";", 1)[0].strip().lower()
            hazards = FEMA_HAZARDS.get(incident, set())
            if hazards:
                items.append(OfficialItem("FEMA", f"{incident} declaration", hazards, when, state=fields["state"],
                                          window=timedelta(days=30), rank=1))
        elif kind in ("STORM REPORT", "RIVER FLOOD") and lat is not None:
            found = hazards(message.raw_content) or ({"flood"} if kind == "RIVER FLOOD" else set())
            if found:
                items.append(OfficialItem("NWS", "storm report" if kind == "STORM REPORT" else "river gauge", found,
                                          when, lat, lon, radius_km=50 if kind == "STORM REPORT" else 60,
                                          window=timedelta(hours=12) if kind == "STORM REPORT" else timedelta(days=2),
                                          rank=3))
        elif kind == "TROPICAL CYCLONE" and lat is not None:
            items.append(OfficialItem("NHC", message.raw_content.split(":", 1)[-1].split("—", 1)[0].strip(),
                                      hazards(message.raw_content) | {"wind", "flood"}, when, lat, lon, radius_km=400,
                                      window=timedelta(days=2), rank=3))
        elif kind == "FIRE DETECTION" and lat is not None:
            items.append(OfficialItem("NASA FIRMS", "satellite fire detection", {"fire"}, when, lat, lon, radius_km=30,
                                      window=timedelta(days=1), rank=1))
        elif kind == "IPAWS ALERT" and resolver is not None and hazards(message.raw_content):
            place = next((location for location in resolver.resolve(message) if location.level in SPECIFIC_LEVELS), None)
            if place is not None:
                items.append(OfficialItem(fields.get("sender") or "IPAWS", "public alert", hazards(message.raw_content),
                                          when, place.lat, place.lon, radius_km=50, window=timedelta(days=1), rank=2))
        elif kind == "FAA TFR" and resolver is not None:
            place = next((location for location in resolver.resolve(message) if location.level in SPECIFIC_LEVELS | {"state"}), None)
            if place is not None and place.level != "state":
                items.append(OfficialItem("FAA", "security flight restriction", {"security"}, when, place.lat, place.lon,
                                          radius_km=50, window=timedelta(days=7), rank=1))
    return items


def hazards(text: str) -> set[str]:
    lowered = text.lower()
    return {family for family, pattern in _HAZARD_PATTERNS.items() if pattern.search(lowered)}


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _covers(alert: dict, lat: float, lon: float, county: str | None) -> bool:
    geometry = alert.get("geometry")
    if geometry:
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        return any(_point_in_polygon(lat, lon, [[tuple(point) for point in ring] for ring in polygon])
                   for polygon in polygons)
    return county is not None and county in alert.get("counties", ())


def corroborate(report_text: str, locations, received_at: datetime, alerts: list[dict],
                official: list[OfficialItem] = ()) -> Corroboration | None:
    """The strongest official confirmation of a report: an NWS alert, else the best other item."""
    report_hazards = hazards(report_text)
    if not report_hazards:
        return None
    received = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
    places = [location for location in locations if location.level in SPECIFIC_LEVELS]
    states = {location.state for location in locations if getattr(location, "state", None)}
    confirmations = []
    nws = _corroborate_nws(report_hazards, places, received, alerts)
    if nws:
        confirmations.append((10, nws))
    for item in official:
        if not item.hazards & report_hazards or abs(item.time - received) > item.window:
            continue
        if item.state:
            matched = item.state in states
        else:
            matched = any(_distance_km(place.lat, place.lon, item.lat, item.lon) <= item.radius_km for place in places)
        if matched:
            lead = int((item.time - received).total_seconds() // 60)
            confirmations.append((item.rank, Corroboration(item.event, "", item.time.isoformat(), lead, item.source)))
    if not confirmations:
        return None
    confirmations.sort(key=lambda pair: -pair[0])
    best = confirmations[0][1]
    return Corroboration(best.event, best.severity, best.sent, best.lead_minutes, best.source, len(confirmations) - 1)


def _corroborate_nws(report_hazards, places, received: datetime, alerts: list[dict]) -> Corroboration | None:
    if not places or not alerts:
        return None
    gazetteer = load_gazetteer()
    counties = {(location.lat, location.lon): gazetteer.county_at(location.lat, location.lon) for location in places}
    best = None
    rank = {"Unknown": 0, "Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}
    for alert in alerts:
        if not hazards(alert.get("event", "")) & report_hazards:
            continue
        # Only an alert active around the report's time: starting at most a day after it
        # and not already ended before it (an alert for today says nothing about last week).
        start = _parse_time(alert.get("onset") or alert.get("sent") or "")
        end = _parse_time(alert.get("ends") or "")
        if (start and start - received > timedelta(hours=24)) or (end and end < received - timedelta(hours=1)):
            continue
        if start and received - start > timedelta(days=3) and not end:
            continue
        if not any(_covers(alert, location.lat, location.lon, counties[(location.lat, location.lon)])
                   for location in places):
            continue
        if best is None or rank.get(alert.get("severity"), 0) > rank.get(best.get("severity"), 0):
            best = alert
    if best is None:
        return None
    start = _parse_time(best.get("onset") or best.get("sent") or "")
    lead = int((start - received).total_seconds() // 60) if start else None
    return Corroboration(best.get("event", "Alert"), best.get("severity", ""), best.get("sent", ""), lead)
