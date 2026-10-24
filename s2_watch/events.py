"""Events: the reports (radio and official) that describe one real-world happening.

Duplicate detection already merges copies of the same report. An event goes further:
a StatRep, a JS8Call message, an NWS warning and a FEMA declaration about one flood are
separate reports but one event. A newsworthy report joins an event when it is the same
category and kind of hazard, close in time to the event's latest report, and close in
place (or in the same state, when only the state is known and the hazard matches).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from s2_watch.corroboration import hazards as hazard_families

EVENT_GAP = timedelta(hours=48)       # a report this long after the last one starts a new event
DEFAULT_RADIUS_KM = 100
RADIUS_KM = {"earthquake": 150, "fire": 80, "tropical": 250, "winter": 150, "heat": 200}
SPECIFIC_LEVELS = {"precise", "city", "county", "grid"}
INCIDENT_GAP = timedelta(days=30)     # a named fire is one event however long it burns
SINGLE_SOURCE_AFTER = timedelta(hours=2)   # unconfirmed this long from one source: say so
STALE_AFTER = timedelta(hours=12)     # no reports this long: the event shows as quiet (Display setting)


def set_quiet_hours(hours: int) -> None:
    global STALE_AFTER
    STALE_AFTER = timedelta(hours=max(1, int(hours)))
# Words too common to show two reports are about the same thing.
STOP_WORDS = set("""
THE AND FOR WITH FROM THAT THIS HAVE HAS WERE WILL BEEN THEY THEIR THERE ABOUT AFTER BEFORE INTO
OVER UNDER NEAR WHEN WHAT WHICH WHILE ALSO MORE MOST SOME SAID SAYS NEWS REPORT REPORTS UPDATE
COUNTY CITY STATE AREA LOCAL TODAY MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY SATURDAY SUNDAY
MAGNET MSG STATREP ALERT COORDINATES UNDERWAY FIRST DEGREE YEAR YEARS OLD MAN MEN WOMAN WOMEN PEOPLE
PERSON PERSONS POLICE SHERIFF DEPUTIES OFFICERS OFFICIALS AUTHORITIES SUSPECT SUSPECTS MURDER MURDERS
KILLED KILLS KILLING DEAD DEATH DEATHS SHOOTING SHOOTINGS SHOT SHOTS INJURED WOUNDED ARREST ARRESTED
ARRESTS CHARGED CHARGES CHARGE CASE TRIAL COURT JUDGE CRIME CRIMES FATAL FATALLY STABBING STABBED
SAYS AMID AGAINST AMERICAN NATIONAL FEDERAL GOVERNMENT PRESIDENT TRUMP""".split())
# State names are places, not story details.
_STATE_WORDS: set[str] | None = None


def _state_words() -> set[str]:
    global _STATE_WORDS
    if _STATE_WORDS is None:
        from s2_watch.locating import load_gazetteer

        _STATE_WORDS = {word for name in load_gazetteer().state_names.values() for word in name.upper().split()}
    return _STATE_WORDS


@dataclass
class Place:
    """Where a report is: a point when known, else just its state."""

    lat: float | None = None
    lon: float | None = None
    level: str = ""
    state: str = ""
    region: int | None = None

    @property
    def has_point(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass
class EventCandidate:
    event_id: int
    category: str
    hazards: set[str]
    place: Place
    first_seen: datetime
    last_seen: datetime
    incident: str = ""
    terms: set[str] = field(default_factory=set)


@dataclass
class EventSummary:
    event_id: int
    title: str
    category: str
    state: str
    region: int | None
    first_seen: datetime
    last_seen: datetime
    reports: int
    sources: tuple[str, ...]
    urgency: str
    official: str                  # e.g. "NWS Flood Warning" or "USGS" ("" = none yet)
    radio_lead_minutes: int | None  # radio's head start on the first official source
    statuses: tuple[str, ...] = field(default_factory=tuple)
    points: list[tuple[float, float]] = field(default_factory=list)
    closed_at: datetime | None = None   # an all-clear report, or the operator, closed it
    close_reason: str = ""
    closed_by: str = ""                 # "report" or "operator"
    exercise: bool = False              # its reports are exercise traffic
    independent_sources: int = 0        # distinct stations and outlets (copies and relays count once)

    def single_source(self, now: datetime) -> bool:
        """Radio reported it, but only one station (or outlet) has, no official source has
        confirmed it, and it has had SINGLE_SOURCE_AFTER to be. (A news story alone isn't
        flagged: most are one article, and analysts read them as such.)"""
        radio = any(source != "OSINT" for source in self.sources)
        return (radio and not self.official and self.closed_at is None and self.independent_sources == 1
                and now - self.first_seen >= SINGLE_SOURCE_AFTER)

    def status(self, now: datetime) -> str:
        """"closed", "quiet" (no reports for STALE_AFTER), or "active"."""
        if self.closed_at is not None:
            return "closed"
        return "quiet" if now - self.last_seen >= STALE_AFTER else "active"

    def status_text(self, now: datetime) -> str:
        state = self.status(now)
        if state == "closed":
            return f"Closed: {self.close_reason}" if self.close_reason else "Closed"
        if state == "quiet":
            return f"Quiet {quiet_hours(now - self.last_seen)} h"
        return "Active"


def quiet_hours(gap: timedelta) -> int:
    return int(gap.total_seconds() // 3600)


# A report saying it's over closes its event. A negation just before ("not yet restored",
# "no all clear") keeps the event open.
_ALL_CLEAR = re.compile(
    r"\ball[-\s]+clear\b"
    r"|\b(?:power|service|electricity|water|phones?|internet|comms|communications|cell\s+service)\s+"
    r"(?:has\s+been\s+|have\s+been\s+|is\s+|are\s+|was\s+|were\s+)?(?:fully\s+)?"
    r"(?:restored|back\s+(?:on(?:line)?|up))\b"
    r"|\b(?:warning|watch|advisory|alert|evacuation(?:\s+orders?)?|curfew|lockdown|boil[-\s]+water\s+"
    r"(?:notice|advisory|order)|shelter[-\s]+in[-\s]+place(?:\s+order)?)\s+"
    r"(?:has\s+been\s+|have\s+been\s+|was\s+|were\s+|is\s+|are\s+)?"
    r"(?:cancell?ed|lifted|expired|rescinded|terminated|ended)\b"
    r"|\b(?:road|roads|highway|interstate|bridge|airport|port|school|schools)\s+"
    r"(?:has\s+been\s+|have\s+been\s+|is\s+|are\s+)?re-?opened\b"
    r"|\b100\s*%\s+contain(?:ed|ment)\b|\bfully\s+contained\b"
    r"|\bsuspects?\s+(?:is\s+|are\s+|was\s+|were\s+|now\s+)?(?:in\s+custody|apprehended|arrested)\b"
    r"|\bthreat\s+(?:has\s+)?(?:passed|ended|is\s+over)\b"
    r"|\b(?:situation|incident)\s+(?:is\s+|has\s+been\s+)?(?:resolved|over)\b"
    r"|\breturn(?:ed|ing)?\s+to\s+normal\b"
    r"|\bno\s+further\s+(?:traffic|reports?|updates?|threat)\b"
    r"|\b(?:eoc|net|nets|operations?)\s+(?:has\s+been\s+|have\s+been\s+|is\s+)?(?:stood\s+down|deactivated|secured)\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\b(?:not|no|isn'?t|aren'?t|wasn'?t|never|yet\s+to\s+be|until|before)\s+(?:\w+\s+){0,2}$",
                      re.IGNORECASE)


def all_clear(text: str) -> str:
    """The words that say an event is over ("power restored", "warning cancelled"), or ""."""
    for match in _ALL_CLEAR.finditer(text):
        if not _NEGATED.search(text[max(0, match.start() - 40):match.start()]):
            return " ".join(match.group().lower().split())
    return ""


def place_from_locations(locations) -> Place:
    """The most specific point among resolved locations, with its state and region."""
    point = next((location for location in locations if location.level in SPECIFIC_LEVELS), None)
    area = next((location for location in locations if location.state or location.region), None)
    if point is not None:
        return Place(point.lat, point.lon, point.level, point.state or (area.state if area else "") or "",
                     point.region if point.region is not None else (area.region if area else None))
    return Place(level=area.level if area else "", state=(area.state or "") if area else "",
                 region=area.region if area else None)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(x, math.radians(lat2 - lat1)) * 6371


def report_hazards(text: str) -> set[str]:
    # "security" (president, VIP...) appears throughout political news, so it would tie
    # unrelated stories together; it still matters for official confirmation.
    return hazard_families(text) - {"security"}


def incident_key(message) -> str:
    """An official incident's own identity: a NIFC fire's name, a USGS quake's id."""
    kind = message.fields.get("type", "")
    if kind == "WILDFIRE" and ":" in message.raw_content:
        name = message.raw_content.split(":", 1)[1].split("—", 1)[0].strip().lower()
        return f"fire:{name}|{message.fields.get('state', '')}" if name else ""
    if kind == "EARTHQUAKE" and message.fields.get("message_id"):
        return f"quake:{message.fields['message_id']}"
    if kind == "FAA TFR" and message.fields.get("message_id"):
        return f"tfr:{message.fields['message_id']}"
    # One storm, volcano, river gauge, state outage, airport or worldwide disaster is one
    # event however many updates it has; a kind of space weather is one event while it lasts.
    for kind_name, field_name, prefix in (("TROPICAL CYCLONE", "storm_id", "storm"), ("VOLCANO", "volcano", "volcano"),
                                          ("RIVER FLOOD", "gauge", "gauge"), ("INTERNET OUTAGE", "entity", "outage"),
                                          ("AIRPORT STATUS", "airport", "airport"), ("WORLD DISASTER", "event", "gdacs")):
        if kind == kind_name and message.fields.get(field_name):
            return f"{prefix}:{message.fields[field_name]}"
    if kind == "SPACE WEATHER" and message.fields.get("scale"):
        return f"space:{message.fields['scale'][0]}"
    return ""


def key_terms(text: str) -> set[str]:
    """The distinctive words of a report, to tell different stories in one place apart:
    not common news words, state names, or the publisher after "|" in a headline."""
    story = " ".join(part.split(" | ")[0] for part in text.splitlines())
    return {word for word in re.findall(r"[A-Z][A-Z'-]{3,}", story.upper())
            if word not in STOP_WORDS and word not in _state_words()}


def clean_title(title: str, place: Place) -> str:
    """A headline with the coordinates placeholder replaced by the state's (or country's) name."""
    if "Coordinates in report" not in title:
        return title
    from s2_watch.locating import load_gazetteer

    state = place.state or ""
    name = load_gazetteer().state_names.get(state, "") or (state if len(state) > 2 else "") or (
        f"{place.lat:.2f}, {place.lon:.2f}" if place.has_point else "")
    return title.replace(" — Coordinates in report", f" — {name}" if name else "").replace("Coordinates in report", name)


def belongs(candidate: EventCandidate, category: str, hazards: set[str], place: Place, seen: datetime,
            incident: str = "", terms: set[str] | None = None) -> bool:
    """Whether a report (category, hazards, place, time) is part of this event."""
    if candidate.category != category:
        return False
    if candidate.incident and incident:
        return candidate.incident == incident   # two named fires are never one event
    if seen - candidate.last_seen > EVENT_GAP or candidate.first_seen - seen > EVENT_GAP:
        return False
    if candidate.hazards and hazards and not candidate.hazards & hazards:
        return False   # a flood and a fire in one county are two events
    shared = candidate.hazards & hazards
    if not candidate.hazards and not hazards:
        # No physical hazard (crime, politics, economy...): being nearby isn't enough;
        # the reports must also tell the same story.
        terms = terms or set()
        common = candidate.terms & terms
        if len(common) < 2 or len(common) < 0.4 * min(len(candidate.terms), len(terms)):
            return False
    if candidate.place.has_point and place.has_point:
        radius = max((RADIUS_KM.get(family, DEFAULT_RADIUS_KM) for family in shared or hazards or candidate.hazards),
                     default=DEFAULT_RADIUS_KM)
        return distance_km(candidate.place.lat, candidate.place.lon, place.lat, place.lon) <= radius
    if not candidate.hazards and not hazards:
        # The same story (checked above): same state, or national news with no state at all.
        if place.state or candidate.place.state:
            return place.state == candidate.place.state
        common = candidate.terms & (terms or set())
        return len(common) >= 0.6 * min(len(candidate.terms), len(terms or set()))
    # Only a state is known for one side: same state and the same kind of hazard.
    return bool(shared) and bool(place.state) and place.state == candidate.place.state


def choose_event(candidates: list[EventCandidate], category: str, hazards: set[str], place: Place,
                 seen: datetime, incident: str = "", terms: set[str] | None = None) -> EventCandidate | None:
    """The best matching event: the same incident, else the nearest with a point, else the latest."""
    matching = [candidate for candidate in candidates
                if belongs(candidate, category, hazards, place, seen, incident, terms)]
    if not matching:
        return None
    same_incident = [candidate for candidate in matching if incident and candidate.incident == incident]
    if same_incident:
        return same_incident[0]
    if place.has_point:
        with_points = [candidate for candidate in matching if candidate.place.has_point]
        if with_points:
            return min(with_points, key=lambda c: distance_km(c.place.lat, c.place.lon, place.lat, place.lon))
    return max(matching, key=lambda candidate: candidate.last_seen)
