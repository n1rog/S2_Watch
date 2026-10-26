"""APRS bulletins (BLN0–BLN9, BLNx groups) from APRS-IS, read-only.

A background thread keeps a connection to an APRS-IS server, logged in receive-only (an
unverified login, passcode -1, sends nothing to RF) with a filter for bulletins. Servers
refuse anonymous logins (N0CALL), so the login is the operator's call sign with an SSID
(Configuration → Sources; -13 by default, to stay clear of the operator's own APRS
software). Only U.S. stations' bulletins are kept, unless the coverage
(Configuration → Coverage) includes areas beyond the U.S. APRS-IS
carries live traffic only: there is no backlog to read, so S2 Watch shows what arrives
while it runs (and the saved reports, as for every source). Bulletins repeat every few
minutes; each is handed over once a day.

A bulletin carries no position, so each station heard sending one is added to the server
filter (b/CALL) and its position beacons are read too; the bulletin then carries the
station's latitude and longitude (in its text and as lat/lon fields). A bulletin from a
station whose position isn't known yet waits up to POSITION_WAIT for its next beacon, then
goes through without one.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from s2_watch import __version__
from s2_watch.models import Message

APPLICATION = "APRS"
DEFAULT_SERVER = "rotate.aprs2.net:14580"
_BULLETIN = re.compile(r"^([A-Z0-9-]{1,9})>[^:]*::(BLN[A-Z0-9]{1,6})\s*:(.*?)(?:\{[A-Za-z0-9]{1,5})?\s*$")
REPEAT_WINDOW = timedelta(hours=24)
POSITION_WAIT = timedelta(minutes=10)
MAX_WATCHED = 100           # stations whose beacons the filter asks for
# Uncompressed position: "!", "=" or, after a 7-character time, "/" or "@".
_POSITION = re.compile(r"^([A-Z0-9-]{1,9})>[^:]*:(?:[!=]|[/@]\d{6}[zh/])"
                       r"(\d{2})(\d{2}\.\d{1,2})\s*([NS]).(\d{3})(\d{2}\.\d{1,2})\s*([EW])")
# Compressed position: symbol table, 4 base-91 latitude, 4 longitude characters.
_COMPRESSED = re.compile(r"^([A-Z0-9-]{1,9})>[^:]*:(?:[!=]|[/@]\d{6}[zh/])([/\\A-Za-j])([!-{]{4})([!-{]{4})")
POSITION_TEXT = re.compile(r"\s*\[[A-Z0-9-]+ position: lat -?[\d.]+, lon -?[\d.]+\]$")


def parse_position(line: str) -> tuple[str, float, float] | None:
    """A position beacon's station and (lat, lon), or None. Mic-E packets aren't read."""
    line = line.strip()
    match = _POSITION.match(line)
    if match:
        station, lat_d, lat_m, ns, lon_d, lon_m, ew = match.groups()
        lat = (int(lat_d) + float(lat_m) / 60) * (-1 if ns == "S" else 1)
        lon = (int(lon_d) + float(lon_m) / 60) * (-1 if ew == "W" else 1)
    else:
        match = _COMPRESSED.match(line)
        if not match:
            return None
        station, _table, lat_text, lon_text = match.groups()

        def base91(text):
            return sum((ord(char) - 33) * 91 ** (3 - i) for i, char in enumerate(text))
        lat = 90 - base91(lat_text) / 380926
        lon = -180 + base91(lon_text) / 190463
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return station, round(lat, 4), round(lon, 4)


def with_position(message: Message, lat: float, lon: float) -> Message:
    """A bulletin with its station's position, in the text and as fields."""
    station = message.fields.get("station", "")
    return replace(message, raw_content=f"{message.raw_content} [{station} position: lat {lat:.4f}, lon {lon:.4f}]",
                   fields={**message.fields, "lat": f"{lat:.4f}", "lon": f"{lon:.4f}"})
US_CALL = re.compile(r"^(?:A[A-L]|[KNW])[A-Z]?\d")     # U.S. stations (APRS-IS carries the world)


def parse_bulletin(line: str, when: datetime, us_only: bool = True) -> Message | None:
    """A bulletin as a Message; with ``us_only``, only from U.S. stations."""
    match = _BULLETIN.match(line.strip())
    if not match:
        return None
    station, bulletin, text = match.group(1), match.group(2), " ".join(match.group(3).split())
    if not text or (us_only and not US_CALL.match(station)):
        return None
    return Message(APPLICATION, f"{station}: {bulletin}: {text}", received_at=when, fields={
        "type": "BULLETIN", "sender": station.split("-")[0], "station": station, "bulletin": bulletin,
        "message_id": f"{station}|{bulletin}|{text}",
    })


class AprsIsAdapter:
    application_name = APPLICATION

    def __init__(self, server: str = DEFAULT_SERVER, login: str = "") -> None:
        host, _, port = server.partition(":")
        self.host, self.port = host or "rotate.aprs2.net", int(port or 14580)
        self.login = login.strip().upper()
        self.status = "Connecting" if self.login else "Needs your call sign (Configuration → Sources)"
        self._pending: list[Message] = []
        self._waiting: list[tuple[datetime, Message]] = []     # bulletins waiting for a position
        self._positions: dict[str, tuple[float, float]] = {}   # station -> its latest position
        self._watched: list[str] = []                          # stations in the b/ filter
        self._seen: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        if self.login:
            self._thread.start()

    def _run(self) -> None:
        wait = 10
        while not self._stop.is_set():
            try:
                self._session()
                wait = 10
            except OSError as error:
                self.status = f"Disconnected: {error}"[:120]
            if self._stop.wait(wait):
                return
            wait = min(wait * 2, 600)       # back off while the server can't be reached

    def _session(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=30) as connection:
            self._socket = connection
            connection.recv(512)                # the server's greeting comes before the login
            connection.sendall(f"user {self.login} pass -1 vers S2-Watch {__version__} filter {self.filter()}\r\n".encode())
            self.status = f"Connected ({self.host})"
            buffer = b""
            refused = None
            connection.settimeout(60)
            while not self._stop.is_set():
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    connection.sendall(b"#keepalive\r\n")
                    continue
                if not chunk:
                    raise OSError("server closed the connection")
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip()
                    if "not allowed" in line.lower() or "unauthorized" in line.lower():
                        refused = line.lstrip("# ")
                    elif line and not line.startswith("#"):
                        self._received(line)
                if refused:
                    raise OSError(f"server refused the login: {refused}")

    def filter(self) -> str:
        """Bulletins, and the beacons of the stations that sent them."""
        return "g/BLN*" + (" b/" + "/".join(self._watched) if self._watched else "")

    def _watch(self, station: str) -> None:
        """Ask the server for this station's position beacons too."""
        if station in self._watched:
            return
        self._watched = (self._watched + [station])[-MAX_WATCHED:]
        connection = self._socket
        if connection is not None:
            try:
                connection.sendall(f"#filter {self.filter()}\r\n".encode())
            except OSError:
                pass

    def _received(self, line: str) -> None:
        now = datetime.now(timezone.utc)
        from s2_watch import world

        position = parse_position(line)
        if position is not None:
            station, lat, lon = position
            with self._lock:
                self._positions[station] = (lat, lon)
            return
        message = parse_bulletin(line, now, us_only=world.COVERAGE == {"us"})
        if message is None:
            return
        key = message.fields["message_id"]
        with self._lock:
            seen = self._seen.get(key)
            if seen and now - seen < REPEAT_WINDOW:
                return
            self._seen = {k: t for k, t in self._seen.items() if now - t < REPEAT_WINDOW}
            self._seen[key] = now
            station = message.fields["station"]
            if station in self._positions:
                self._pending.append(with_position(message, *self._positions[station]))
            else:
                self._waiting.append((now, message))
        self._watch(station)

    def read_messages(self) -> list[Message]:
        now = datetime.now(timezone.utc)
        with self._lock:
            still = []
            for since, message in self._waiting:
                position = self._positions.get(message.fields["station"])
                if position is not None:
                    self._pending.append(with_position(message, *position))
                elif now - since >= POSITION_WAIT:
                    self._pending.append(message)          # no beacon heard: without a position
                else:
                    still.append((since, message))
            self._waiting = still
            messages, self._pending = self._pending, []
        return messages

    def close(self) -> None:
        self._stop.set()
        connection = self._socket
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
