"""Data for the optional map overlays: NWS alerts and station coverage.

NWS alerts come from api.weather.gov, fetched on a background thread every few
minutes while the overlay is shown. Most alerts name whole counties (SAME/FIPS codes)
rather than carrying an outline; the map draws those from its bundled county outlines.

Station coverage is built from every message read (not just those a watch form
matches): the last time each 4-character grid square was heard from, so the map can
show where the network is reporting and where it has gone quiet.
"""

import json
from pathlib import Path
import math
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import timezone

from PySide6.QtCore import QObject, QTimer, Signal

from s2_watch import __version__
from s2_watch.geo import maidenhead_to_coordinates
from s2_watch.icao_countries import registration_country
from s2_watch.models import Message

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?status=actual"
# api.weather.gov asks every client to identify itself.
USER_AGENT = f"S2-Watch/{__version__} (https://github.com/n1rog/S2_Watch)"
SEVERITY_ORDER = {"Unknown": 0, "Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}
DESCRIPTION_LIMIT = 900


def _round_coordinates(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, list):
        return [_round_coordinates(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_coordinates(item) for key, item in value.items()}
    return value


def compact_alerts(payload: dict) -> list[dict]:
    """Only what the map shows, lowest severity first so the worst draw on top."""
    alerts = []
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        geometry = feature.get("geometry")
        # SAME codes are "0" + the county's 5-digit FIPS code; marine zones do not map to counties.
        counties = [code[1:] for code in props.get("geocode", {}).get("SAME", []) if len(code) == 6]
        if not geometry and not counties:
            continue
        description = (props.get("description") or "").strip()
        if len(description) > DESCRIPTION_LIMIT:
            description = description[:DESCRIPTION_LIMIT].rsplit(" ", 1)[0] + " …"
        alerts.append({
            "id": props.get("id", ""),
            "event": props.get("event", "Alert"),
            "severity": props.get("severity") or "Unknown",
            "urgency": props.get("urgency", ""),
            "certainty": props.get("certainty", ""),
            "headline": props.get("headline") or props.get("event", ""),
            "area": props.get("areaDesc", ""),
            "onset": props.get("onset") or props.get("effective") or "",
            "sent": props.get("sent") or props.get("effective") or "",
            "ends": props.get("ends") or props.get("expires") or "",
            "sender": props.get("senderName", ""),
            "description": description,
            "geometry": _round_coordinates(geometry) if geometry else None,
            "counties": [] if geometry else counties,
        })
    alerts.sort(key=lambda alert: SEVERITY_ORDER.get(alert["severity"], 0))
    return alerts


def fetch_nws_alerts(timeout: float = 20) -> list[dict]:
    request = urllib.request.Request(
        NWS_ALERTS_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return compact_alerts(json.loads(response.read()))


class NwsAlertsPoller(QObject):
    """Fetches NWS alerts off the UI thread while enabled; delivers them via signals."""

    alerts_ready = Signal(object)  # list[dict]
    failed = Signal(str)

    def __init__(self, interval_ms: int = 5 * 60 * 1000, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.fetch)
        self._busy = False

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self._timer.isActive():
            self._timer.start()
            self.fetch()
        elif not enabled:
            self._timer.stop()

    def fetch(self) -> None:
        if self._busy:
            return
        self._busy = True

        def work() -> None:
            try:
                self.alerts_ready.emit(fetch_nws_alerts())
            except Exception as error:  # network errors, bad JSON: report and try again later
                self.failed.emit(str(error))
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()


# A CommStat/JS8 status line carries the station's grid after its first comma:
# "W1NEM: @MAGNET  ,EM29RL,3,Y48,..."
_STATUS_GRID = re.compile(r",([A-R]{2}[0-9]{2}(?:[A-X]{2})?),\d+,", re.IGNORECASE)


def station_grid(message: Message) -> str | None:
    """The sending station's 4-character grid square, if the message says where it is."""
    for key in ("grid", "gridsquare", "GRID"):
        value = (message.fields.get(key) or "").strip()
        if len(value) >= 4 and maidenhead_to_coordinates(value[:4]):
            return value[:4].upper()
    match = _STATUS_GRID.search(message.raw_content)
    if match and maidenhead_to_coordinates(match.group(1)[:4]):
        return match.group(1)[:4].upper()
    return None


class CoverageTracker:
    """When each grid square was last heard from, by which stations."""

    def __init__(self) -> None:
        self.cells: dict[str, dict] = {}
        self.changed = False
        # Re-scans read the review window again; count each message once.
        self._counted: set[int] = set()

    def add(self, message: Message) -> None:
        grid = station_grid(message)
        if not grid:
            return
        identity = hash((message.source_application, message.received_at, message.raw_content))
        if identity in self._counted:
            return
        self._counted.add(identity)
        when = message.received_at if message.received_at.tzinfo else message.received_at.replace(tzinfo=timezone.utc)
        cell = self.cells.setdefault(grid, {"first": when, "last": when, "messages": 0, "stations": Counter()})
        cell["first"] = min(cell["first"], when)
        cell["last"] = max(cell["last"], when)
        cell["messages"] += 1
        sender = message.fields.get("sender") or message.fields.get("from") or ""
        if not sender:
            head = message.raw_content.split(":", 1)[0].strip()
            sender = head if re.fullmatch(r"[A-Z0-9/]{3,10}", head) else ""
        if sender:
            cell["stations"][sender.upper()] += 1
        self.changed = True

    def snapshot(self) -> list[dict]:
        self.changed = False
        result = []
        for grid, cell in self.cells.items():
            lon = (ord(grid[0]) - ord("A")) * 20 - 180 + int(grid[2]) * 2
            lat = (ord(grid[1]) - ord("A")) * 10 - 90 + int(grid[3])
            result.append({
                "grid": grid, "south": lat, "west": lon,
                "first": cell["first"].astimezone(timezone.utc).isoformat(timespec="seconds"),
                "last": cell["last"].astimezone(timezone.utc).isoformat(timespec="seconds"),
                "messages": cell["messages"],
                "stations": [station for station, _count in cell["stations"].most_common(6)],
            })
        return result


# ---- Aircraft (ADS-B) -----------------------------------------------------------------
# Community ADS-B aggregators with free, keyless APIs; adsb.fi is used if adsb.lol fails.
ADSB_SOURCES = (
    ("adsb.lol", "https://api.adsb.lol/v2/mil", "https://api.adsb.lol/v2/sqk/{squawk}",
     "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"),
    ("adsb.fi", "https://opendata.adsb.fi/api/v2/mil", "https://opendata.adsb.fi/api/v2/sqk/{squawk}",
     "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{radius}"),
)
EMERGENCY_SQUAWKS = {"7500": "Hijack", "7600": "Radio failure", "7700": "Emergency"}
MAX_RADIUS_NM = 250
# Air-ambulance callsigns (ADS-B flight IDs). Medical flights flying under their tail
# number cannot be recognised this way; they still show, as helicopters or planes.
MEDICAL_CALLSIGN = re.compile(
    r"^(?:LIFE|MEDEVAC|MEDIC|EVAC|AIREVAC|AIRLIFE|AIRMED|CAREFL|CFLT|REACH|MERCY|LIFEGRD|LIFEGUARD|"
    r"FLTFORLIFE|FFL\d|GUARDIAN|GUARDN|STATMED|MEDFLT|ANGEL|HEARTNET|AMBUL)",
    re.IGNORECASE,
)
# Owners/operators of air-ambulance aircraft (tar1090-db / adsb.fi "ownOp"). Whole words,
# so "Los Angeles", "Angel ..." and "Hospitality" don't count.
AIR_MEDICAL_OPERATOR = re.compile(
    r"\b(?:AIR METHODS|AIR EVAC EMS|PHI HEALTH|PHI AIR MEDICAL|LIFE ?FLIGHT|LIFEFLIGHT|MED-?TRANS CORP|MED TRANS CORP|"
    r"REACH AIR MEDICAL|METRO AVIATION|GUARDIAN FLIGHT|CAREFLITE|CARE ?FLIGHT|MED ?FLIGHT|MERCY FLIGHTS?|MERCY AIR|"
    r"STAT MEDEVAC|MEDEVAC|AIR AMBULANCE|AIR MEDICAL|AIRMEDICAL|LIFENET|LIFE ?STAR|AIRLIFE|CALSTAR|"
    r"HOSPITAL|MEDICAL CENTER|HEALTH SYSTEM|CHILDRENS HOSPITAL|HEALTH SCIENCES CENTER)\b",
    re.IGNORECASE,
)
NOT_AIR_MEDICAL = re.compile(r"ANIMAL HOSPITAL|FLYING CLUB|WEST METRO AVIATION|PORTLAND METRO|DEVELOPMENT LLC|"
                             r"HOSPITALITY|PRECIOUS LIFE|SECOND LIFE", re.IGNORECASE)


def is_air_medical_operator(name: str) -> bool:
    return bool(name and AIR_MEDICAL_OPERATOR.search(name) and not NOT_AIR_MEDICAL.search(name))
_AIR_AMBULANCE: dict[str, str] | None = None


def air_ambulance_operator(hex_code: str) -> str:
    """The air-medical operator an aircraft is registered to (bundled list), or ""."""
    global _AIR_AMBULANCE
    if _AIR_AMBULANCE is None:
        path = Path(__file__).parent / "data" / "air_ambulance.json"
        try:
            _AIR_AMBULANCE = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _AIR_AMBULANCE = {}
    return _AIR_AMBULANCE.get(hex_code.lower(), "")


HELICOPTER_TYPES = {
    "EC35", "EC45", "EC30", "EC20", "EC25", "EC55", "EC75", "H135", "H145", "H160", "AS50", "AS55", "AS65",
    "B06", "B407", "B412", "B429", "B505", "A109", "A119", "A139", "A169", "S76", "S92", "R22", "R44", "R66",
    "H60", "H47", "UH1", "EN48", "MD52", "MD60", "BK17",
}


def _get_json(url: str, timeout: float = 15) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def compact_aircraft(record: dict) -> dict | None:
    if record.get("lat") is None or record.get("lon") is None:
        return None
    squawk = str(record.get("squawk") or "")
    emergency = EMERGENCY_SQUAWKS.get(squawk)
    if not emergency and record.get("emergency") not in (None, "", "none"):
        emergency = str(record["emergency"]).replace("_", " ").capitalize()
    altitude = record.get("alt_baro")
    return {
        "hex": record.get("hex", ""),
        "flight": (record.get("flight") or "").strip(),
        "registration": record.get("r") or "",
        "type": record.get("t") or "",
        "lat": round(float(record["lat"]), 4),
        "lon": round(float(record["lon"]), 4),
        "altitude": "ground" if altitude == "ground" else (int(altitude) if isinstance(altitude, (int, float)) else None),
        "speed": round(record["gs"]) if isinstance(record.get("gs"), (int, float)) else None,
        "track": round(record["track"]) if isinstance(record.get("track"), (int, float)) else None,
        "squawk": squawk,
        "military": bool((record.get("dbFlags") or 0) & 1),
        "emergency": emergency or "",
        # Medevac: an air-ambulance callsign, or an aircraft registered to an air-medical
        # operator (tail-number flights), from the bundled list or the feed's own owner field.
        "medical": bool(MEDICAL_CALLSIGN.match((record.get("flight") or "").strip())
                        or air_ambulance_operator(record.get("hex", ""))
                        or is_air_medical_operator(record.get("ownOp") or "")),
        "operator": air_ambulance_operator(record.get("hex", "")) or (record.get("ownOp") or "").strip(),
        "country": registration_country(record.get("hex", "")),
        # Drawing shape: helicopter, light aircraft (general aviation), or airliner-size.
        "shape": "helicopter" if (record.get("category") == "A7" or (record.get("t") or "") in HELICOPTER_TYPES)
                 else "light" if record.get("category") in ("A1", "A2", "B1", "B4") else "plane",
    }


# Be a considerate client of free community services: space requests out, refresh the
# nationwide queries less often than local traffic, and back off when rate limited.
REQUEST_SPACING_S = 1.1
# All traffic nationwide at every zoom level: the whole U.S. every 3 minutes (36 spaced
# requests, ~3 minutes and ~8 MB a sweep), the focus regions every minute, a zoomed-in view every 30 seconds.
REFRESH_S = {"military": 60, "squawks": 60, "local": 30, "focus": 60, "national": 180}
# Alaska and Hawaii, which the lower-48 grid leaves out: Anchorage, Fairbanks, Juneau, Honolulu.
OUTSIDE_LOWER_48_TILES = [(61.2, -149.9, MAX_RADIUS_NM), (64.8, -147.7, MAX_RADIUS_NM),
                          (58.3, -134.4, MAX_RADIUS_NM), (21.0, -157.5, MAX_RADIUS_NM)]
BACKOFF_S = (60, 120, 300)


class RateLimited(OSError):
    pass


def _part_urls(part: str, view, military_url: str, squawk_url: str, point_url: str) -> list[str]:
    if part == "military":
        return [military_url]
    if part == "squawks":
        return [squawk_url.format(squawk=squawk) for squawk in EMERGENCY_SQUAWKS]
    circles = view if part in ("focus", "national") else [view]
    return [point_url.format(lat=round(lat, 3), lon=round(lon, 3), radius=int(min(MAX_RADIUS_NM, max(5, radius))))
            for lat, lon, radius in circles]


def _fetch_part(part: str, view) -> tuple[list[dict], str]:
    """One kind of query ("military", "squawks", "local", "focus", "national").

    ``view`` is the circle, or list of circles, for the area kinds. Each request tries the
    aggregators in turn, so one failed request costs only its own circle; the part fails
    only when every request did.
    """
    per_source = [(name, _part_urls(part, view, military, squawk, point))
                  for name, military, squawk, point in ADSB_SOURCES]
    records: list[dict] = []
    errors: list[str] = []
    used: list[str] = []
    limited = False
    for index in range(len(per_source[0][1])):
        if index:
            time.sleep(REQUEST_SPACING_S)
        for attempt, (name, urls) in enumerate(per_source):
            if attempt:
                time.sleep(REQUEST_SPACING_S)
            try:
                data = _get_json(urls[index])
            except urllib.error.HTTPError as error:
                limited = limited or error.code == 429
                errors.append(f"{name}: HTTP {error.code}")
                continue
            except Exception as error:
                errors.append(f"{name}: {error}")
                continue
            # adsb.lol calls the list "ac"; adsb.fi calls it "aircraft".
            records.extend(data.get("ac") or data.get("aircraft") or [])
            if name not in used:
                used.append(name)
            break
    if not used:
        raise (RateLimited if limited else OSError)("; ".join(dict.fromkeys(errors)))
    return records, " + ".join(used)


def focus_tiles(regions, spacing_nm: float = 340) -> list[tuple[float, float, float]]:
    """Query circles (lat, lon, radius nm) covering the lower-48 parts of MARS ``regions``.

    Circles 250 nm in radius on a ~340 nm grid overlap enough to cover everything between
    them; circles that reach none of the region's states are dropped.
    """
    from s2_watch.locating import load_gazetteer

    gazetteer = load_gazetteer()
    tiles: list[tuple[float, float, float]] = []
    for region in sorted(regions):
        anchors = [anchor for state, anchor in gazetteer.state_anchor.items()
                   if gazetteer.state_region.get(state) == region and state not in ("AK", "HI")]
        if not anchors:  # Alaska/Hawaii-only coverage: use those states
            anchors = [anchor for state, anchor in gazetteer.state_anchor.items()
                       if gazetteer.state_region.get(state) == region]
        if not anchors:
            continue
        south = min(lat for lat, _lon in anchors) - 3
        north = max(lat for lat, _lon in anchors) + 3
        west = min(lon for _lat, lon in anchors) - 4
        east = max(lon for _lat, lon in anchors) + 4
        step_lat = spacing_nm / 60
        lat = south + step_lat / 2
        while lat < north:
            step_lon = spacing_nm / (60 * max(0.2, math.cos(math.radians(lat))))
            lon = west + step_lon / 2
            while lon < east:
                near = any(math.hypot(lat - a_lat, (lon - a_lon) * math.cos(math.radians(lat))) * 60 <= 330
                           for a_lat, a_lon in anchors)
                if near and not any(math.hypot(lat - t_lat, (lon - t_lon) * math.cos(math.radians(lat))) * 60 < 150
                                    for t_lat, t_lon, _r in tiles):
                    tiles.append((round(lat, 2), round(lon, 2), MAX_RADIUS_NM))
                lon += step_lon
            lat += step_lat
    return tiles


def _in_regions(record: dict, regions: set[int]) -> bool:
    from s2_watch.locating import load_gazetteer

    lat, lon = record.get("lat"), record.get("lon")
    if lat is None or lon is None:
        return False
    gazetteer = load_gazetteer()
    return any(gazetteer.in_region(region, float(lat), float(lon)) for region in regions)


def fetch_aircraft(view: tuple[float, float, float] | None = None) -> tuple[list[dict], str]:
    """All three queries at once (used for testing); the poller refreshes them separately."""
    records, source = [], ""
    for part in ("military", "squawks") + (("local",) if view else ()):
        part_records, source = _fetch_part(part, view)
        records.extend(part_records)
        time.sleep(REQUEST_SPACING_S)
    return merge_aircraft(records), source


def merge_aircraft(records) -> list[dict]:
    aircraft: dict[str, dict] = {}
    for record in records:
        compact = compact_aircraft(record)
        if compact and compact["hex"]:
            aircraft[compact["hex"]] = compact
    return list(aircraft.values())


class AircraftPoller(QObject):
    """Refreshes all U.S. traffic (every type, at every zoom level) off the UI thread."""

    aircraft_ready = Signal(object, str)  # list[dict], source name
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.fetch)
        self._view_timer = QTimer(self)  # wait until the map has stopped moving
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(3000)
        self._view_timer.timeout.connect(self._view_settled)
        self._busy_lanes: set[str] = set()
        self.view: tuple[float, float, float] | None = None
        self._pending_view: tuple[float, float, float] | None = None
        self.focus: list[tuple[float, float, float]] = []   # query circles covering the focus regions
        self.focus_regions: set[int] = set()
        self.national: list[tuple[float, float, float]] = []   # computed on first use
        self.general_traffic = True   # False: only military and emergencies are wanted
        self._parts: dict[str, tuple[float, list[dict]]] = {}   # part -> (fetched at, records)
        self._source = ""
        self._backoff_until = 0.0
        self._backoff_step = 0

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self._timer.isActive():
            self._timer.start()
            self.fetch()
        elif not enabled:
            self._timer.stop()
            self._view_timer.stop()

    def set_general_traffic(self, wanted: bool) -> None:
        """Fetch all traffic (True) or only military aircraft and emergency squawks."""
        if wanted != self.general_traffic:
            self.general_traffic = wanted
            if self._timer.isActive():
                self.fetch()

    def set_view(self, lat: float, lon: float, radius_nm: float) -> None:
        """The zoomed-in map area to show all traffic for; radius 0 means zoomed out."""
        self._pending_view = (lat, lon, radius_nm) if radius_nm > 0 else None
        self._view_timer.start()

    def set_focus(self, regions) -> None:
        """Always fetch all traffic in the operator's focus regions (MARS region numbers)."""
        self.focus_regions = set(regions)
        self.focus = focus_tiles(self.focus_regions)
        self._parts.pop("focus", None)
        if self._timer.isActive():
            self.fetch()

    def _view_settled(self) -> None:
        if self._pending_view != self.view:
            self.view = self._pending_view
            self._parts.pop("local", None)
            if self._timer.isActive():
                self.fetch()

    def fetch(self) -> None:
        if not self.national:
            self.national = focus_tiles(range(1, 11)) + OUTSIDE_LOWER_48_TILES
        general = self.general_traffic
        wanted = {"military": True, "squawks": True, "local": general and bool(self.view),
                  "focus": general and bool(self.focus), "national": general}
        for part, keep in wanted.items():
            if not keep:
                self._parts.pop(part, None)
        # The nationwide sweep takes minutes, so it runs in its own lane and never holds up
        # the quick parts (military, emergencies, focus regions, the zoomed-in view).
        for lane, parts in (("quick", ("military", "squawks", "focus", "local")), ("national", ("national",))):
            self._fetch_lane(lane, [part for part in parts if wanted[part]])

    def _fetch_lane(self, lane: str, parts: list[str]) -> None:
        now = time.monotonic()
        if lane in self._busy_lanes or now < self._backoff_until:
            return
        due = [part for part in parts if now - self._parts.get(part, (-1e9, []))[0] >= REFRESH_S[part]]
        if not due:
            return
        self._busy_lanes.add(lane)
        areas = {"focus": list(self.focus), "national": list(self.national), "local": self.view}
        focus_regions = set(self.focus_regions)

        def work() -> None:
            try:
                for index, part in enumerate(due):
                    if index:
                        time.sleep(REQUEST_SPACING_S)
                    records, self._source = _fetch_part(part, areas[part] if part in areas else None)
                    if part == "focus":
                        # The query circles spill over the region edges; keep only traffic inside.
                        records = [record for record in records if _in_regions(record, focus_regions)]
                    self._parts[part] = (time.monotonic(), records)
                self._backoff_step = 0
                merged = merge_aircraft(record for _at, records in list(self._parts.values()) for record in records)
                self.aircraft_ready.emit(merged, self._source)
            except RateLimited as error:
                wait = BACKOFF_S[min(self._backoff_step, len(BACKOFF_S) - 1)]
                self._backoff_step += 1
                self._backoff_until = time.monotonic() + wait
                self.failed.emit(f"rate limited by the ADS-B services, retrying in {wait // 60} min ({error})")
            except Exception as error:
                self.failed.emit(str(error))
            finally:
                self._busy_lanes.discard(lane)

        threading.Thread(target=work, daemon=True, name=f"adsb-{lane}").start()
