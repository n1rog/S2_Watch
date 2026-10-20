"""APRS bulletins (BLN0–BLN9, BLNx groups) from APRS-IS, read-only.

A background thread keeps a connection to an APRS-IS server, logged in receive-only (an
unverified login, passcode -1, sends nothing to RF) with a filter for bulletins. Servers
refuse anonymous logins (N0CALL), so the login is the operator's call sign with an SSID
(Configuration → Sources; -13 by default, to stay clear of the operator's own APRS
software). Only U.S. stations' bulletins are kept. APRS-IS
carries live traffic only: there is no backlog to read, so S2 Watch shows what arrives
while it runs (and the saved reports, as for every source). Bulletins repeat every few
minutes; each is handed over once a day.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from s2_watch import __version__
from s2_watch.models import Message

APPLICATION = "APRS"
DEFAULT_SERVER = "rotate.aprs2.net:14580"
_BULLETIN = re.compile(r"^([A-Z0-9-]{1,9})>[^:]*::(BLN[A-Z0-9]{1,6})\s*:(.*?)(?:\{[A-Za-z0-9]{1,5})?\s*$")
REPEAT_WINDOW = timedelta(hours=24)
US_CALL = re.compile(r"^(?:A[A-L]|[KNW])[A-Z]?\d")     # U.S. stations (APRS-IS carries the world)


def parse_bulletin(line: str, when: datetime) -> Message | None:
    match = _BULLETIN.match(line.strip())
    if not match:
        return None
    station, bulletin, text = match.group(1), match.group(2), " ".join(match.group(3).split())
    if not text or not US_CALL.match(station):
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
            connection.sendall(f"user {self.login} pass -1 vers S2-Watch {__version__} filter g/BLN*\r\n".encode())
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

    def _received(self, line: str) -> None:
        now = datetime.now(timezone.utc)
        message = parse_bulletin(line, now)
        if message is None:
            return
        key = message.fields["message_id"]
        with self._lock:
            seen = self._seen.get(key)
            if seen and now - seen < REPEAT_WINDOW:
                return
            self._seen = {k: t for k, t in self._seen.items() if now - t < REPEAT_WINDOW}
            self._seen[key] = now
            self._pending.append(message)

    def read_messages(self) -> list[Message]:
        with self._lock:
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
