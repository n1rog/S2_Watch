"""Official and open-source reports as an "OSINT" feeder, alongside the radio applications.

Sources (all free, no key; each fetched on its own schedule on a background thread):
- GDELT news search: US news about protests, riots, unrest, shootings, bombings and
  terrorism in the last day (news-derived, not official).
- FAA temporary flight restrictions of type SECURITY (VIP movements, security events).
- U.S. State Department travel advisories updated recently (threats to Americans abroad).
- FEMA disaster and emergency declarations.

DHS NTAS, CISA and U.S. Embassy alert feeds are not included: their servers refuse
automated clients, and S2 Watch does not disguise itself to get around that.

Items become ordinary Messages (application "OSINT"), so watch forms, the analyzer,
deduplication, locations, labels and Metrics treat them like radio reports.
"""

import csv
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from s2_watch import __version__
from s2_watch.models import Message

APPLICATION = "OSINT"
USER_AGENT = f"S2-Watch/{__version__} (https://github.com/n1rog/S2_Watch)"
GDELT_QUERY = ('(protest OR protesters OR riot OR unrest OR "civil unrest" OR looting OR curfew OR shooting '
               'OR "active shooter" OR bombing OR "bomb threat" OR terrorism OR terrorist OR "mass casualty") '
               "sourcecountry:US sourcelang:english")
GDELT_URL = ("https://api.gdeltproject.org/api/v2/doc/doc?" +
             urllib.parse.urlencode({"query": GDELT_QUERY, "mode": "ArtList", "format": "json",
                                     "maxrecords": 75, "timespan": "24h", "sort": "DateDesc"}))
FAA_TFR_URL = "https://tfr.faa.gov/tfrapi/exportTfrList"
STATE_ADVISORIES_URL = "https://travel.state.gov/_res/rss/TAsTWs.xml"
FEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson"
SPC_URLS = ("https://www.spc.noaa.gov/climo/reports/today_filtered.csv",
            "https://www.spc.noaa.gov/climo/reports/yesterday_filtered.csv")
NIFC_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/"
            "FeatureServer/0/query?" + urllib.parse.urlencode({
                # Wildfires (not prescribed burns) of 100+ acres that are not yet fully contained.
                "where": "IncidentSize >= 100 AND IncidentTypeCategory = 'WF' "
                         "AND (PercentContained < 100 OR PercentContained IS NULL)",
                "outFields": "IncidentName,IncidentSize,PercentContained,POOState,FireDiscoveryDateTime,"
                             "ModifiedOnDateTime_dt,UniqueFireIdentifier",
                "returnGeometry": "true", "outSR": "4326", "f": "json"}))
# US states and territories, roughly: quakes elsewhere only count when magnitude 6+.
US_BOXES = ((24, 50, -125, -66), (51, 72, -180, -129), (18, 23, -161, -154), (17, 19, -68, -65), (13, 21, 144, 146))
# Minutes between fetches of each source.
# GDELT refuses frequent requests ("429 Too Many Requests"): every 30 minutes.
SCHEDULE = {"gdelt": 30, "faa": 30, "state": 60, "fema": 60, "usgs": 10, "spc": 15, "nifc": 60}
DECLARATION_TYPES = {"DR": "Major Disaster", "EM": "Emergency", "FM": "Fire Management"}
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}

# Shared by every adapter instance, so the re-scans that follow a watch-form change reuse
# recent results instead of fetching again: source -> (fetched at, messages).
_CACHE: dict[str, tuple[float, list[Message]]] = {}
_UPDATED: dict[str, datetime] = {}      # source -> when it last fetched successfully (UTC)
_CACHE_LOCK = threading.Lock()


def _get(url: str, timeout: float = 25) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fetch_gdelt() -> list[Message]:
    data = json.loads(_get(GDELT_URL) or b"{}")
    messages = []
    for article in data.get("articles", []):
        title = (article.get("title") or "").strip()
        if not title:
            continue
        try:
            seen = datetime.strptime(article.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            seen = datetime.now(timezone.utc)
        messages.append(Message(APPLICATION, title, received_at=seen, fields={
            "type": "NEWS", "sender": article.get("domain", ""), "message_id": article.get("url", title),
            "url": article.get("url", ""), "source": "GDELT news",
        }))
    return messages


def fetch_faa_security_tfrs() -> list[Message]:
    messages = []
    for tfr in json.loads(_get(FAA_TFR_URL)):
        if (tfr.get("type") or "").upper() != "SECURITY":
            continue
        try:
            created = datetime.strptime(tfr.get("creation_date", ""), "%m/%d/%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            created = datetime.now(timezone.utc)
        notam = tfr.get("notam_id", "")
        text = f"FAA security flight restriction (TFR {notam}): {tfr.get('description', '').strip()}"
        messages.append(Message(APPLICATION, text, received_at=created, fields={
            "type": "FAA TFR", "sender": "FAA", "message_id": f"TFR {notam}", "state": tfr.get("state", ""),
            "url": f"https://tfr.faa.gov/tfr3/?page=detail_{notam.replace('/', '_')}", "source": "FAA",
        }))
    return messages


def _feed_date(text: str) -> datetime | None:
    """RSS pubDate, with or without a time of day ("Tue, 15 Sep 2026")."""
    try:
        return _utc(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.strptime(text.strip(), "%a, %d %b %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_state_advisories(days: int = 30) -> list[Message]:
    """Advisories issued or updated in the last ``days`` (the feed lists every country)."""
    root = ElementTree.fromstring(_get(STATE_ADVISORIES_URL))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    messages = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        published = _feed_date(item.findtext("pubDate") or "")
        if published is None:
            continue
        if not title or published < cutoff:
            continue
        description = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
        description = " ".join(description.split())
        if "no changes to the advisory level" in description.lower():
            continue  # reissued without a change: not news
        description = description[:400]
        messages.append(Message(APPLICATION, f"State Dept travel advisory: {title}. {description}", received_at=published,
                                fields={"type": "TRAVEL ADVISORY", "sender": "U.S. Department of State",
                                        "message_id": f"{title}|{published.date()}", "url": item.findtext("link") or "",
                                        "source": "State Dept"}))
    return messages


def fetch_fema_declarations(days: int = 30) -> list[Message]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    query = urllib.parse.urlencode({"$filter": f"declarationDate ge '{since}'", "$orderby": "declarationDate desc",
                                    "$top": 1000})
    records = json.loads(_get(f"{FEMA_URL}?{query}")).get("DisasterDeclarationsSummaries", [])
    disasters: dict[str, dict] = {}
    for record in records:
        key = f"{record.get('declarationType', '')}-{record.get('disasterNumber', '')}"
        disaster = disasters.setdefault(key, {**record, "areas": []})
        disaster["areas"].append(record.get("designatedArea", ""))
    messages = []
    for key, disaster in disasters.items():
        state = disaster.get("state", "")
        kind = DECLARATION_TYPES.get(disaster.get("declarationType", ""), "Disaster")
        areas = [area for area in disaster["areas"] if area]
        text = (f"FEMA {kind} declaration {key}: {disaster.get('declarationTitle', '').title()} — "
                f"{STATE_NAMES.get(state, state)}; incident type {disaster.get('incidentType', '')}; "
                f"{len(areas)} area{'s' if len(areas) != 1 else ''} designated")
        try:
            declared = _utc(datetime.fromisoformat(disaster.get("declarationDate", "").replace("Z", "+00:00")))
        except ValueError:
            declared = datetime.now(timezone.utc)
        messages.append(Message(APPLICATION, text, received_at=declared, fields={
            "type": "FEMA DECLARATION", "sender": "FEMA", "message_id": key, "state": state, "source": "FEMA",
        }))
    return messages


def _position(lat: float, lon: float) -> str:
    # Written as decimal coordinates so the location finder places the item exactly.
    return f"({lat:.3f}, {lon:.3f})"


def fetch_usgs_earthquakes() -> list[Message]:
    messages = []
    for quake in json.loads(_get(USGS_URL)).get("features", []):
        props, (lon, lat, _depth) = quake["properties"], quake["geometry"]["coordinates"]
        magnitude = props.get("mag") or 0
        in_us = any(south <= lat <= north and west <= lon <= east for south, north, west, east in US_BOXES)
        if not in_us and magnitude < 6:
            continue
        when = datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc)
        text = f"USGS: M{magnitude:.1f} earthquake — {props.get('place', '')} {_position(lat, lon)}"
        # Every quake can confirm a radio report; only notable ones become reports themselves.
        notable = magnitude >= (4.0 if in_us else 6.0)
        messages.append(Message(APPLICATION, text, received_at=when, fields={
            "type": "EARTHQUAKE", "sender": "USGS", "message_id": quake.get("id", text), "magnitude": f"{magnitude:.1f}",
            "notable": "1" if notable else "",
            "lat": f"{lat}", "lon": f"{lon}", "url": props.get("url", ""), "source": "USGS",
        }))
    return messages


def fetch_spc_storm_reports() -> list[Message]:
    """Tornado, wind and hail reports from the Storm Prediction Center (today and yesterday).

    SPC days run 12Z to 12Z and times are UTC ("1440" = 14:40Z); hail sizes are in
    hundredths of an inch, wind speeds in mph ("UNK" when estimated from damage).
    """
    messages = []
    # SPC's "today" is the convective day that began at the most recent 12Z.
    today = (datetime.now(timezone.utc) - timedelta(hours=12)).date()
    for days_back, url in enumerate(SPC_URLS):
        day = today - timedelta(days=days_back)
        kind, measure = "", ""
        for row in csv.reader(_get(url).decode("utf-8", "replace").splitlines()):
            if not row:
                continue
            if row[0] == "Time":
                kind = {"F_Scale": "Tornado", "Speed": "Damaging wind", "Size": "Hail"}.get(row[1], "Storm")
                measure = row[1]
                continue
            if len(row) < 8:
                continue
            time_text, value, place, county, state, lat, lon, comments = row[:8]
            try:
                hour, minute = int(time_text[:2]), int(time_text[2:])
                report_day = day if hour >= 12 else day + timedelta(days=1)
                when = datetime(report_day.year, report_day.month, report_day.day, hour, minute, tzinfo=timezone.utc)
                lat_value, lon_value = float(lat), float(lon)
            except ValueError:
                continue
            detail = {"F_Scale": f"rated {value}" if value not in ("", "UNK") else "",
                      "Speed": f"{value} mph" if value not in ("", "UNK") else "",
                      "Size": f"{int(value) / 100:.2f} in" if value.isdigit() else ""}.get(measure, "")
            text = (f"SPC storm report: {kind}{' ' + detail if detail else ''} — {place}, {county} County, {state} "
                    f"{_position(lat_value, lon_value)}. {comments}")
            messages.append(Message(APPLICATION, text, received_at=when, fields={
                "type": "SPC STORM REPORT", "sender": "NOAA SPC", "message_id": f"{kind}|{when.isoformat()}|{lat}|{lon}",
                "state": state, "lat": lat, "lon": lon, "source": "SPC",
            }))
    return messages


def fetch_nifc_wildfires() -> list[Message]:
    """Active wildfires of 100 acres or more (prescribed burns excluded); one item a day per fire."""
    messages = []
    for feature in json.loads(_get(NIFC_URL)).get("features", []):
        attributes, geometry = feature.get("attributes", {}), feature.get("geometry") or {}
        if "x" not in geometry:
            continue
        stamp = attributes.get("ModifiedOnDateTime_dt") or attributes.get("FireDiscoveryDateTime") or 0
        modified = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc) if stamp else datetime.now(timezone.utc)
        name = (attributes.get("IncidentName") or "Unnamed").strip().title()
        state = (attributes.get("POOState") or "").replace("US-", "")
        contained = attributes.get("PercentContained")
        text = (f"NIFC wildfire: {name} Fire — {STATE_NAMES.get(state, state)}; "
                f"{int(attributes.get('IncidentSize') or 0):,} acres"
                + (f", {int(contained)}% contained" if contained is not None else "")
                + f" {_position(geometry['y'], geometry['x'])}")
        discovered = attributes.get("FireDiscoveryDateTime") or 0
        new_fire = discovered and datetime.now(timezone.utc).timestamp() - discovered / 1000 < 3 * 86400
        notable = (attributes.get("IncidentSize") or 0) >= 10_000 or bool(new_fire)
        messages.append(Message(APPLICATION, text, received_at=modified, fields={
            "type": "WILDFIRE", "sender": "NIFC", "notable": "1" if notable else "", "state": state, "lat": f"{geometry['y']}", "lon": f"{geometry['x']}",
            "message_id": f"{attributes.get('UniqueFireIdentifier', name)}|{modified.date()}", "source": "NIFC",
        }))
    return messages


def cached_messages(kinds=None) -> list[Message]:
    """Everything the OSINT sources last fetched (optionally only some item types)."""
    with _CACHE_LOCK:
        messages = [message for _fetched, items in _CACHE.values() for message in items]
    return [message for message in messages if kinds is None or message.fields.get("type") in kinds]


# What each source is, in the operator's words (Configuration → Watch Forms), in display order.
SOURCE_LABELS = {
    "spc": "NOAA SPC storm reports (tornado, hail, damaging wind)",
    "usgs": "USGS earthquakes",
    "nifc": "NIFC wildfires",
    "fema": "FEMA disaster declarations",
    "faa": "FAA security flight restrictions (TFRs)",
    "state": "State Department travel advisories",
    "gdelt": "News reports of unrest and violence (GDELT)",
}


def forget(name: str) -> None:
    """Drop a source's fetched items (it was turned off): they leave the map and confirmations."""
    with _CACHE_LOCK:
        _CACHE.pop(name, None)
        _UPDATED.pop(name, None)


def last_updated(name: str) -> datetime | None:
    with _CACHE_LOCK:
        return _UPDATED.get(name)


from s2_watch.adapters import osint_more  # noqa: E402  (the newer sources)

FETCHERS = {"gdelt": fetch_gdelt, "faa": fetch_faa_security_tfrs, "state": fetch_state_advisories,
            "fema": fetch_fema_declarations, "usgs": fetch_usgs_earthquakes, "spc": fetch_spc_storm_reports,
            "nifc": fetch_nifc_wildfires, **osint_more.FETCHERS}
SOURCE_LABELS.update(osint_more.SOURCE_LABELS)
SCHEDULE.update(osint_more.SCHEDULE)
DEFAULT_OFF = set(osint_more.DEFAULT_OFF)


class OsintAdapter:
    """Collects OSINT items on a background thread; read_messages hands over new ones."""

    application_name = APPLICATION

    def __init__(self, lookback: timedelta | None = None, fetchers=None) -> None:
        self.lookback = lookback
        self.fetchers = FETCHERS if fetchers is None else fetchers
        self.status = "Connecting"
        self.errors: dict[str, str] = {}
        if not self.fetchers:
            self._update_status()
        self._pending: list[Message] = []
        self._delivered: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            for name, fetch in self.fetchers.items():
                if self._stop.is_set():
                    return
                self._collect(name, fetch)
            self._stop.wait(60)

    def _collect(self, name: str, fetch) -> None:
        with _CACHE_LOCK:
            fetched_at, messages = _CACHE.get(name, (0.0, []))
        if time.monotonic() - fetched_at >= SCHEDULE.get(name, 30) * 60 or not fetched_at:
            try:
                messages = fetch()
                with _CACHE_LOCK:
                    _CACHE[name] = (time.monotonic(), messages)
                    _UPDATED[name] = datetime.now(timezone.utc)
                self.errors.pop(name, None)
            except Exception as error:  # keep the other sources going; retry later
                self.errors[name] = str(error)[:120]
                # Rate limited: wait 30 minutes (retrying sooner prolongs the block); otherwise 5.
                retry_s = 1800 if "429" in str(error) else 300
                with _CACHE_LOCK:
                    _CACHE[name] = (time.monotonic() - SCHEDULE.get(name, 30) * 60 + retry_s, messages)
        cutoff = datetime.now(timezone.utc) - self.lookback if self.lookback else None
        with self._lock:
            for message in messages:
                identity = f"{name}|{message.fields.get('message_id')}"
                if identity in self._delivered or (cutoff and _utc(message.received_at) < cutoff):
                    continue
                if message.fields.get("notable") == "":
                    continue  # kept for corroboration only (e.g. small quakes and fires)
                self._delivered.add(identity)
                self._pending.append(message)
        self._update_status()

    def _update_status(self) -> None:
        if not self.fetchers:
            self.status = "Off (no OSINT sources chosen)"
            return
        working = [source for source in self.fetchers if source not in self.errors]
        self.status = ("Connected (" + ", ".join(working) + ")" if working else "Unavailable") + (
            f"; failing: {', '.join(self.errors)}" if self.errors else "")

    def read_messages(self) -> list[Message]:
        with self._lock:
            messages, self._pending = self._pending, []
        return messages

    def close(self) -> None:
        self._stop.set()
