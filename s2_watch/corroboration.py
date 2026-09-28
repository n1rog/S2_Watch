"""Match radio reports to official NWS alerts in effect at the same place.

A report is corroborated when an active alert covers one of its locations (inside the
alert's outline, or in one of the counties it was issued for) and both are about the
same kind of hazard. Locations must be specific (coordinates, grid, city, or county):
a state or MARS region is too coarse to confirm anything. When the radio report came
before the alert was issued, that lead time is noted: early reporting is the point of
an S2 network.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from s2_watch.locating import _point_in_polygon, load_gazetteer

# Hazard families, recognised in report text and in NWS event names.
HAZARDS = {
    "flood": ("flood", "flash flood", "high water", "rising water"),
    "heat": ("heat", "triple digit", "hot "),
    "fire": ("fire", "red flag", "smoke", "burn"),
    "winter": ("winter", "snow", "blizzard", "ice ", "icy", "freez", "frost", "cold", "chill", "sleet"),
    "wind": ("wind", "gust", "dust", "derecho"),
    "tornado": ("tornado", "funnel"),
    "thunderstorm": ("thunderstorm", "tstorm", "t-storm", "hail", "lightning", "severe storm", "severe weather"),
    "tropical": ("hurricane", "tropical", "storm surge", "typhoon"),
    "fog": ("fog", "visibility"),
    "air": ("air quality", "smoke", "aqi"),
    "coastal": ("surf", "rip current", "coastal", "tsunami"),
    "earthquake": ("earthquake", "quake"),
    "avalanche": ("avalanche",),
}
SPECIFIC_LEVELS = {"precise", "city", "county", "grid"}


@dataclass(frozen=True)
class Corroboration:
    event: str
    severity: str
    sent: str
    lead_minutes: int | None   # how long before the alert the radio report came in

    def text(self) -> str:
        lead = f" · radio {self.lead_minutes} min earlier" if self.lead_minutes and self.lead_minutes > 0 else ""
        return f"✓ NWS {self.event}{lead}"


def hazards(text: str) -> set[str]:
    lowered = f" {text.lower()} "
    return {family for family, words in HAZARDS.items() if any(word in lowered for word in words)}


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


def corroborate(report_text: str, locations, received_at: datetime, alerts: list[dict]) -> Corroboration | None:
    """The most severe active alert that covers the report's place and matches its hazard."""
    report_hazards = hazards(report_text)
    places = [location for location in locations if location.level in SPECIFIC_LEVELS]
    if not report_hazards or not places or not alerts:
        return None
    gazetteer = load_gazetteer()
    counties = {(location.lat, location.lon): gazetteer.county_at(location.lat, location.lon) for location in places}
    best = None
    rank = {"Unknown": 0, "Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}
    for alert in alerts:
        if not hazards(alert.get("event", "")) & report_hazards:
            continue
        if not any(_covers(alert, location.lat, location.lon, counties[(location.lat, location.lon)])
                   for location in places):
            continue
        if best is None or rank.get(alert.get("severity"), 0) > rank.get(best.get("severity"), 0):
            best = alert
    if best is None:
        return None
    sent = _parse_time(best.get("sent") or best.get("onset") or "")
    received = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
    lead = int((sent - received).total_seconds() // 60) if sent else None
    return Corroboration(best.get("event", "Alert"), best.get("severity", ""), best.get("sent", ""), lead)
