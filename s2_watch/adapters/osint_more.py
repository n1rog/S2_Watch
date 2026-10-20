"""More OSINT sources (S2 Watch 0.29): local public alerts, space weather, hurricanes,
internet outages, NWS storm reports, river flooding, airport disruptions, volcanoes,
worldwide disasters, exploited vulnerabilities, border crossings and satellite fire
detections. Each fetcher returns Messages like the ones in osint.py; all are free, and all
but NASA FIRMS (a free map key) need no key.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from s2_watch.models import Message

APPLICATION = "OSINT"
FIRMS_KEY = ""          # NASA FIRMS map key (Configuration → Sources); empty: that source is skipped


def set_firms_key(key: str) -> None:
    global FIRMS_KEY
    FIRMS_KEY = key.strip()


def _get(url: str) -> bytes:
    from s2_watch.adapters.osint import _get as get

    return get(url)


def _position(lat: float, lon: float) -> str:
    return f"({lat:.3f}, {lon:.3f})"


def _iso(value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


# -- FEMA IPAWS: public alerts from local authorities ------------------------------------
IPAWS_URL = "https://www.fema.gov/api/open/v1/IpawsArchivedAlerts"
_IPAWS_NO_AREA = re.compile(r"affected\s*area|in this area|area|n/?a", re.IGNORECASE)
_IPAWS_SKIP = re.compile(r"\b(?:test|demo|rwt|rmt|administrative|practice)\b", re.IGNORECASE)


def fetch_ipaws(hours: int = 72) -> list[Message]:
    """Evacuations, shelter-in-place orders, law enforcement warnings and other alerts
    authorities send through IPAWS. FEMA's archive runs about a day behind; the weather
    service's own alerts are left out (S2 Watch has them directly)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    query = urllib.parse.urlencode({"$filter": f"sent ge '{since}'", "$orderby": "sent desc", "$top": "1000"})
    data = json.loads(_get(f"{IPAWS_URL}?{query}"))
    messages = []
    for alert in data.get("IpawsArchivedAlerts", []):
        if alert.get("status") != "Actual" or alert.get("msgType") not in ("Alert", "Update"):
            continue
        for info in (alert.get("info") or [])[:1]:
            event = (info.get("event") or "").strip()
            sender = (info.get("senderName") or alert.get("sender") or "").strip()
            if not event or _IPAWS_SKIP.search(event) or re.search(r"\bNWS\b|National Weather Service", sender):
                continue
            areas = "; ".join(area["areaDesc"] for area in info.get("area") or []
                              if area.get("areaDesc") and not _IPAWS_NO_AREA.fullmatch(area["areaDesc"].strip()))
            sender = re.sub(r"^[\d,\s]+", "", sender).strip(" ,") or sender
            words = " ".join((info.get("headline") or info.get("description") or "").split())[:300]
            text = " — ".join(part for part in (f"Public alert (IPAWS): {event}", words, areas) if part)
            messages.append(Message(APPLICATION, text, received_at=_iso(alert["sent"]), fields={
                "type": "IPAWS ALERT", "sender": sender, "message_id": alert.get("identifier", text),
                "source": "FEMA IPAWS", "official": "1",
            }))
    return messages


# -- NOAA SWPC: space weather that affects HF radio --------------------------------------
SWPC_URL = "https://services.swpc.noaa.gov/products/alerts.json"
SWPC_THRESHOLDS = {"R": 2, "S": 2, "G": 3}     # moderate radio blackouts and up, strong storms and up
SWPC_WORDS = {"R": "radio blackout", "S": "solar radiation storm", "G": "geomagnetic storm"}


def fetch_space_weather() -> list[Message]:
    messages = []
    for product in json.loads(_get(SWPC_URL)):
        text = product.get("message") or ""
        scale = re.search(r"NOAA Scale:\s*([RSG])(\d)", text)
        if not scale or int(scale.group(2)) < SWPC_THRESHOLDS[scale.group(1)]:
            continue
        kind = re.search(r"^(ALERT|WARNING|WATCH|EXTENDED WARNING|SUMMARY|CANCEL\w*)[^\n]*", text, re.MULTILINE)
        headline = " ".join(kind.group(0).split()) if kind else "Space weather"
        letter, level = scale.group(1), scale.group(2)
        words = f"Space weather: {headline} — NOAA scale {letter}{level} {SWPC_WORDS[letter]}"
        if letter in "RG":
            words += "; HF radio propagation affected"
        when = datetime.strptime(product["issue_datetime"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        messages.append(Message(APPLICATION, words, received_at=when, fields={
            "type": "SPACE WEATHER", "sender": "NOAA SWPC", "message_id": f"{product.get('product_id')}|{when.isoformat()}",
            "scale": f"{letter}{level}", "url": "https://www.swpc.noaa.gov/", "source": "NOAA SWPC", "official": "1",
        }))
    return messages


# -- National Hurricane Center: active tropical cyclones --------------------------------
NHC_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
NHC_KINDS = {"HU": "Hurricane", "TS": "Tropical Storm", "TD": "Tropical Depression", "STS": "Subtropical Storm",
             "STD": "Subtropical Depression", "PTC": "Potential Tropical Cyclone", "PC": "Post-tropical Cyclone"}


def fetch_hurricanes() -> list[Message]:
    messages = []
    for storm in json.loads(_get(NHC_URL)).get("activeStorms", []):
        kind = NHC_KINDS.get(storm.get("classification", ""), "Tropical cyclone")
        lat, lon = storm.get("latitudeNumeric"), storm.get("longitudeNumeric")
        where = f" {_position(lat, lon)}" if lat is not None and lon is not None else ""
        text = (f"National Hurricane Center: {kind} {storm.get('name', '')} — {storm.get('intensity', '?')} kt winds, "
                f"{storm.get('pressure', '?')} mb, moving {storm.get('movementDir', '?')}° at "
                f"{storm.get('movementSpeed', '?')} mph{where}")
        when = _iso(storm["lastUpdate"]) if storm.get("lastUpdate") else datetime.now(timezone.utc)
        advisory = (storm.get("publicAdvisory") or {}).get("url", "https://www.nhc.noaa.gov/")
        messages.append(Message(APPLICATION, text, received_at=when, fields={
            "type": "TROPICAL CYCLONE", "sender": "NHC", "message_id": f"{storm.get('id')}|{storm.get('lastUpdate')}",
            "storm_id": storm.get("id", ""), "url": advisory, "source": "NHC", "official": "1",
            **({"lat": f"{lat}", "lon": f"{lon}"} if lat is not None and lon is not None else {}),
        }))
    return messages


# -- IODA: internet outages by state -----------------------------------------------------
IODA_URL = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/alerts"


def fetch_internet_outages(hours: int = 24) -> list[Message]:
    """Critical state-level internet outages (IODA, Georgia Tech): the latest alert per state."""
    now = datetime.now(timezone.utc)
    query = urllib.parse.urlencode({"from": int((now - timedelta(hours=hours)).timestamp()), "until": int(now.timestamp()),
                                    "entityType": "region", "relatedTo": "country/US", "limit": 500})
    latest: dict[str, dict] = {}
    for alert in json.loads(_get(f"{IODA_URL}?{query}")).get("data") or []:
        if alert.get("level") != "critical":
            continue
        code = alert["entity"]["code"]
        if code not in latest or alert["time"] > latest[code]["time"]:
            latest[code] = alert
    messages = []
    for code, alert in latest.items():
        state = alert["entity"]["name"]
        share = alert["value"] / alert["historyValue"] if alert.get("historyValue") else 0
        how = {"ping-slash24": "reachable networks", "bgp": "announced networks", "merit-nt": "darknet traffic"}.get(
            alert.get("datasource"), alert.get("datasource", "signal"))
        when = datetime.fromtimestamp(alert["time"], tz=timezone.utc)
        text = f"Internet outage: {state} — {how} at {share:.0%} of normal (IODA, critical)"
        messages.append(Message(APPLICATION, text, received_at=when, fields={
            "type": "INTERNET OUTAGE", "sender": "IODA", "message_id": f"ioda:{code}|{when:%Y-%m-%dT%H}",
            "state": state, "entity": code, "url": f"https://ioda.inetintel.cc.gatech.edu/region/{code}",
            "source": "IODA",
        }))
    return messages


# -- NWS Local Storm Reports (via Iowa State's archive) ---------------------------------
LSR_URL = "https://mesonet.agron.iastate.edu/geojson/lsr.php?hours=24"
# The kinds SPC's storm reports don't already cover (tornado, hail, thunderstorm wind), in plain
# words; a minimum magnitude where small amounts are routine.
LSR_KINDS = {
    "FLASH FLOOD": ("Flash flooding", 0), "FLOOD": ("Flooding", 0), "COASTAL FLOOD": ("Coastal flooding", 0),
    "DEBRIS FLOW": ("Debris flow (mudslide)", 0), "STORM SURGE": ("Storm surge", 0), "TSUNAMI": ("Tsunami", 0),
    "HEAVY SNOW": ("Heavy snowfall", 6), "SNOW": ("Heavy snowfall", 6), "BLIZZARD": ("Blizzard", 0),
    "ICE STORM": ("Ice storm", 0), "FREEZING RAIN": ("Freezing rain icing", 0.25), "HEAVY RAIN": ("Heavy rain", 3),
    "WILDFIRE": ("Wildfire", 0), "DUST STORM": ("Dust storm", 0),
    "NON-TSTM WND DMG": ("Damaging wind (not a thunderstorm)", 0), "LANDSLIDE": ("Landslide", 0),
}


def fetch_storm_reports() -> list[Message]:
    messages = []
    for feature in json.loads(_get(LSR_URL)).get("features", []):
        props = feature["properties"]
        kind = LSR_KINDS.get((props.get("typetext") or "").upper())
        if kind is None:
            continue
        words, minimum = kind
        try:
            magnitude = float(props.get("magnitude") or 0)
        except ValueError:
            magnitude = 0
        if minimum and magnitude < minimum:
            continue
        lat, lon = props.get("lat"), props.get("lon")
        amount = f" {props['magnitude']} {(props.get('unit') or '').lower()}".rstrip() if magnitude else ""
        place = ", ".join(part for part in (props.get("city"), f"{props.get('county')} County" if props.get("county")
                                            else "", props.get("state")) if part)
        reporter = props.get("source") or ""
        ham = " Reported by amateur radio." if "amateur" in reporter.lower() else ""
        remark = f" {props['remark']}" if props.get("remark") else ""
        text = (f"NWS storm report: {words}{amount} — {place} {_position(lat, lon)}.{remark}{ham} "
                f"(source: {reporter}, NWS {props.get('wfo', '')})")
        messages.append(Message(APPLICATION, " ".join(text.split()), received_at=_iso(props["valid"]), fields={
            "type": "STORM REPORT", "sender": "NWS", "message_id": f"{props.get('product_id')}|{lat}|{lon}|{words}",
            "state": props.get("state", ""), "reporter": reporter, "lat": f"{lat}", "lon": f"{lon}",
            "url": "https://mesonet.agron.iastate.edu/lsr/", "source": "NWS LSR",
        }))
    return messages


# -- NWS river gauges at flood stage ------------------------------------------------------
RIVERS_URL = ("https://mapservices.weather.noaa.gov/eventdriven/rest/services/water/riv_gauges/MapServer/0/query?"
              + urllib.parse.urlencode({"where": "status IN ('major','moderate')", "outFields": "*",
                                        "returnGeometry": "false", "f": "json"}))


def fetch_river_flooding() -> list[Message]:
    messages = []
    for feature in json.loads(_get(RIVERS_URL)).get("features", []):
        gauge = feature["attributes"]
        lat, lon = gauge.get("latitude"), gauge.get("longitude")
        status = (gauge.get("status") or "").upper()
        try:
            when = datetime.strptime(gauge["obstime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            when = datetime.now(timezone.utc)
        text = (f"River flooding: {gauge.get('waterbody', '')} at {gauge.get('location', '')}, {gauge.get('state', '')} — "
                f"{status} flood stage, {gauge.get('observed', '?')} {gauge.get('units', 'ft')} "
                f"(flood stage {gauge.get('flood', '?')}) {_position(lat, lon)}")
        messages.append(Message(APPLICATION, text, received_at=when, fields={
            "type": "RIVER FLOOD", "sender": "NWS", "message_id": f"{gauge.get('gaugelid')}|{status}|{when:%Y-%m-%d}",
            "gauge": gauge.get("gaugelid", ""), "state": gauge.get("state", ""), "url": gauge.get("url", ""),
            "lat": f"{lat}", "lon": f"{lon}",
            "source": "NWS river gauges",
        }))
    return messages


# -- FAA airport status: ground stops and closures ---------------------------------------
FAA_NAS_URL = "https://nasstatus.faa.gov/api/airport-status-information"
_ROUTINE_DELAY = re.compile(r"volume|construction|maintenance|rwy|runway|taxi|other", re.IGNORECASE)


def fetch_airport_status() -> list[Message]:
    """Airport closures, and ground stops for reasons other than routine volume or works."""
    root = ElementTree.fromstring(_get(FAA_NAS_URL))
    updated = datetime.now(timezone.utc)
    messages = []
    for program in root.iter("Program"):
        airport, reason = program.findtext("ARPT", "").strip(), program.findtext("Reason", "").strip()
        if airport and not _ROUTINE_DELAY.search(reason):
            until = program.findtext("End_Time", "").strip()
            text = f"FAA: ground stop at {airport} airport — {reason}" + (f", until {until}" if until else "")
            messages.append(_airport(text, airport, f"stop|{airport}|{reason}", updated))
    for delay_type in root.iter("Delay_type"):
        if "closure" not in (delay_type.findtext("Name") or "").lower():
            continue
        for closed in delay_type.iter("Airport"):
            airport, reason = closed.findtext("ARPT", "").strip(), closed.findtext("Reason", "").strip()
            # "!SNA 10/025 ... CLSD DLY 0630-1315": a scheduled closure notice (NOTAM), not news.
            if airport and not reason.startswith("!") and not _ROUTINE_DELAY.search(reason):
                reopen = closed.findtext("Reopen", "").strip()
                text = f"FAA: {airport} airport closed — {reason}" + (f", reopening {reopen}" if reopen else "")
                messages.append(_airport(text, airport, f"closed|{airport}|{reason}", updated))
    return messages


def _airport(text: str, airport: str, key: str, when: datetime) -> Message:
    return Message(APPLICATION, text, received_at=when, fields={
        "type": "AIRPORT STATUS", "sender": "FAA", "message_id": f"{key}|{when:%Y-%m-%d}", "airport": airport,
        "url": "https://nasstatus.faa.gov/", "source": "FAA NAS status", "official": "1",
    })


# -- USGS volcanoes at WATCH or WARNING --------------------------------------------------
VOLCANO_URL = "https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated"


def fetch_volcanoes() -> list[Message]:
    messages = []
    for notice in json.loads(_get(VOLCANO_URL)):
        if notice.get("alertLevel") not in ("WATCH", "WARNING"):
            continue
        lat, lon = notice.get("lat"), notice.get("long")
        text = (f"USGS volcano: {notice.get('vName')} {notice.get('colorCode')}/{notice.get('alertLevel')} — "
                f"{' '.join((notice.get('noticeSynopsis') or '').split())} {_position(lat, lon)}")
        when = _iso(notice["sentUtc"]) if notice.get("sentUtc") else datetime.now(timezone.utc)
        messages.append(Message(APPLICATION, text, received_at=when, fields={
            "type": "VOLCANO", "sender": "USGS", "message_id": notice.get("noticeId", text),
            "volcano": notice.get("vnum", ""), "url": notice.get("noticeUrl", ""), "source": "USGS volcanoes",
            "lat": f"{lat}", "lon": f"{lon}",
        }))
    return messages


# -- GDACS: worldwide disasters (orange and red) -----------------------------------------
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?alertlevel=Orange;Red"
GDACS_KINDS = {"EQ": "earthquake", "TC": "tropical cyclone", "FL": "flood", "VO": "volcano", "WF": "wildfire",
               "DR": "drought", "TS": "tsunami"}


def fetch_world_disasters(days: int = 7) -> list[Message]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    messages = []
    for feature in json.loads(_get(GDACS_URL)).get("features", []):
        props = feature["properties"]
        to_date = _iso(props["todate"]) if props.get("todate") else datetime.now(timezone.utc)
        if to_date < since:
            continue
        lon, lat = feature["geometry"]["coordinates"][:2]
        kind = GDACS_KINDS.get(props.get("eventtype", ""), "disaster")
        text = (f"GDACS {props.get('alertlevel', '').upper()} alert ({kind}): {props.get('name', '')} — "
                f"{props.get('country', '')} {_position(lat, lon)}")
        messages.append(Message(APPLICATION, text, received_at=to_date, fields={
            "type": "WORLD DISASTER", "sender": "GDACS", "message_id": f"{props.get('eventtype')}{props.get('eventid')}|"
                                                                     f"{props.get('episodeid')}",
            "event": f"{props.get('eventtype')}{props.get('eventid')}",
            "url": (props.get("url") or {}).get("report", "https://www.gdacs.org/"), "source": "GDACS",
        }))
    return messages


# -- CISA Known Exploited Vulnerabilities --------------------------------------------------
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def fetch_exploited_vulnerabilities(days: int = 7) -> list[Message]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    messages = []
    for item in json.loads(_get(KEV_URL)).get("vulnerabilities", []):
        try:
            added = datetime.strptime(item["dateAdded"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if added.date() < since:
            continue
        ransomware = " Used in ransomware campaigns." if item.get("knownRansomwareCampaignUse") == "Known" else ""
        text = (f"CISA known exploited vulnerability: {item.get('cveID')} — {item.get('vendorProject')} "
                f"{item.get('product')}: {item.get('vulnerabilityName')}. Agencies must fix it by "
                f"{item.get('dueDate')}.{ransomware}")
        messages.append(Message(APPLICATION, text, received_at=added, fields={
            "type": "CYBER", "sender": "CISA", "message_id": item.get("cveID", text),
            "url": f"https://nvd.nist.gov/vuln/detail/{item.get('cveID')}", "source": "CISA KEV",
        }))
    return messages


# -- CBP: border crossings not open ------------------------------------------------------
CBP_URL = "https://bwt.cbp.gov/api/bwtnew"


def fetch_border_crossings() -> list[Message]:
    now = datetime.now(timezone.utc)
    messages = []
    for port in json.loads(_get(CBP_URL)):
        status = (port.get("port_status") or "").strip()
        if not status or status.lower() == "open" or "24" not in (port.get("hours") or ""):
            continue                    # only round-the-clock crossings: others close every night
        name = " — ".join(part for part in (port.get("port_name"), port.get("crossing_name")) if part)
        text = f"CBP: border crossing {status.lower()} — {name} ({port.get('border', '')})"
        messages.append(Message(APPLICATION, text, received_at=now, fields={
            "type": "BORDER CROSSING", "sender": "CBP", "message_id": f"{port.get('port_number')}|{status}|{now:%Y-%m-%d}",
            "url": "https://bwt.cbp.gov/", "source": "CBP", "official": "1",
        }))
    return messages


# -- NASA FIRMS: satellite fire detections (clusters) ------------------------------------
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/country/csv/{key}/VIIRS_SNPP_NRT/USA/1"
FIRMS_CELL = 0.2          # degrees: detections closer than about 20 km are one cluster
FIRMS_MIN_DETECTIONS = 6  # fewer: a field burn or a flare, not news


def fetch_fire_detections() -> list[Message]:
    """Clusters of VIIRS fire detections in the last day; single hotspots are left out."""
    if not FIRMS_KEY:
        return []
    cells: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in csv.DictReader(_get(FIRMS_URL.format(key=urllib.parse.quote(FIRMS_KEY))).decode("utf-8", "replace").splitlines()):
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
        except (KeyError, ValueError):
            continue
        if row.get("confidence", "n") == "l":
            continue
        cells[(int(lat // FIRMS_CELL), int(lon // FIRMS_CELL))].append({**row, "lat": lat, "lon": lon})
    # Occupied cells that touch (including diagonally) are one fire: a fire across a cell
    # boundary stays whole.
    parent = {cell: cell for cell in cells}

    def root(cell):
        while parent[cell] != cell:
            parent[cell] = parent[parent[cell]]
            cell = parent[cell]
        return cell

    for cell_lat, cell_lon in cells:
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                neighbour = (cell_lat + d_lat, cell_lon + d_lon)
                if neighbour in parent:
                    parent[root(neighbour)] = root((cell_lat, cell_lon))
    clusters: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for cell, hits in cells.items():
        clusters[root(cell)].extend(hits)
    messages = []
    for (cell_lat, cell_lon), hits in clusters.items():
        if len(hits) < FIRMS_MIN_DETECTIONS:
            continue
        lat = sum(hit["lat"] for hit in hits) / len(hits)
        lon = sum(hit["lon"] for hit in hits) / len(hits)
        latest = max(f"{hit.get('acq_date', '')} {hit.get('acq_time', '').zfill(4)}" for hit in hits)
        try:
            when = datetime.strptime(latest, "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            when = datetime.now(timezone.utc)
        text = f"Satellite fire detection: {len(hits)} hotspots, possible wildfire {_position(lat, lon)} (NASA FIRMS)"
        messages.append(Message(APPLICATION, text, received_at=when, fields={
            "type": "FIRE DETECTION", "sender": "NASA FIRMS", "message_id": f"firms:{cell_lat}:{cell_lon}|{when:%Y-%m-%d}",
            "url": "https://firms.modaps.eosdis.nasa.gov/map/", "source": "NASA FIRMS", "lat": f"{lat}", "lon": f"{lon}",
        }))
    return messages


FETCHERS = {
    "ipaws": fetch_ipaws, "space": fetch_space_weather, "nhc": fetch_hurricanes, "ioda": fetch_internet_outages,
    "lsr": fetch_storm_reports, "rivers": fetch_river_flooding, "airports": fetch_airport_status,
    "volcanoes": fetch_volcanoes, "gdacs": fetch_world_disasters, "kev": fetch_exploited_vulnerabilities,
    "cbp": fetch_border_crossings, "firms": fetch_fire_detections,
}
SOURCE_LABELS = {
    "lsr": "NWS storm reports: floods, snow, ice, wildfire (Iowa State archive)",
    "rivers": "NWS river gauges at moderate or major flood stage",
    "nhc": "National Hurricane Center active storms",
    "ipaws": "Local public alerts: evacuations, shelter in place, police (FEMA IPAWS; a day behind, so they "
             "show with a watch period of 2 days or more)",
    "space": "Space weather affecting HF radio (NOAA SWPC)",
    "ioda": "Internet outages by state (IODA, Georgia Tech)",
    "airports": "Airport closures and ground stops (FAA)",
    "volcanoes": "USGS volcanoes at WATCH or WARNING",
    "firms": "Satellite fire detections (NASA FIRMS; needs a free map key)",
    "gdacs": "Worldwide disasters, orange and red (GDACS)",
    "kev": "Cyber: newly exploited vulnerabilities (CISA)",
    "cbp": "Border crossings not open (CBP)",
}
SCHEDULE = {"ipaws": 60, "space": 30, "nhc": 30, "ioda": 30, "lsr": 30, "rivers": 60, "airports": 15,
            "volcanoes": 60, "gdacs": 60, "kev": 360, "cbp": 30, "firms": 60}
# Off until the operator turns them on: outside a U.S. MARS focus, or needing a key.
DEFAULT_OFF = {"gdacs", "kev", "cbp", "firms"}
