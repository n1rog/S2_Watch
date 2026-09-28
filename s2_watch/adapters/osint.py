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
# Minutes between fetches of each source.
SCHEDULE = {"gdelt": 15, "faa": 30, "state": 60, "fema": 60}
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


FETCHERS = {"gdelt": fetch_gdelt, "faa": fetch_faa_security_tfrs, "state": fetch_state_advisories,
            "fema": fetch_fema_declarations}


class OsintAdapter:
    """Collects OSINT items on a background thread; read_messages hands over new ones."""

    application_name = APPLICATION

    def __init__(self, lookback: timedelta | None = None, fetchers=None) -> None:
        self.lookback = lookback
        self.fetchers = fetchers or FETCHERS
        self.status = "Connecting"
        self.errors: dict[str, str] = {}
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
                self._delivered.add(identity)
                self._pending.append(message)
        working = [source for source in self.fetchers if source not in self.errors]
        self.status = ("Connected (" + ", ".join(working) + ")" if working else "Unavailable") + (
            f"; failing: {', '.join(self.errors)}" if self.errors else "")

    def read_messages(self) -> list[Message]:
        with self._lock:
            messages, self._pending = self._pending, []
        return messages

    def close(self) -> None:
        self._stop.set()
