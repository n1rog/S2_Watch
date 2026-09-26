"""Data for the optional map overlays: NWS alerts and station coverage.

NWS alerts come from api.weather.gov, fetched on a background thread every few
minutes while the overlay is shown. Most alerts name whole counties (SAME/FIPS codes)
rather than carrying an outline; the map draws those from its bundled county outlines.

Station coverage is built from every message read (not just those a watch form
matches): the last time each 4-character grid square was heard from, so the map can
show where the network is reporting and where it has gone quiet.
"""

import json
import re
import threading
import urllib.request
from collections import Counter
from datetime import timezone

from PySide6.QtCore import QObject, QTimer, Signal

from s2_watch import __version__
from s2_watch.geo import maidenhead_to_coordinates
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
        cell = self.cells.setdefault(grid, {"last": when, "messages": 0, "stations": Counter()})
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
                "last": cell["last"].astimezone(timezone.utc).isoformat(timespec="seconds"),
                "messages": cell["messages"],
                "stations": [station for station, _count in cell["stations"].most_common(6)],
            })
        return result
