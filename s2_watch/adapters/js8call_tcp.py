import json
import socket
from datetime import datetime, timezone
from typing import Any

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.models import Message


class JS8CallTcpAdapter(ApplicationAdapter):
    """Reads newline-delimited JSON from JS8Call's local TCP API."""

    application_name = "JS8Call"

    def __init__(self, host: str = "127.0.0.1", port: int = 2442) -> None:
        self.host = host
        self.port = port
        self.status = "Disconnected"
        self.last_error: str | None = None
        self._socket: socket.socket | None = None
        self._buffer = b""

    def read_messages(self) -> list[Message]:
        if not self.host:
            self.status = "Not configured"
            return []
        if self._socket is None and not self._connect():
            return []
        try:
            chunk = self._socket.recv(65536)  # type: ignore[union-attr]
        except BlockingIOError:
            return []
        except OSError as error:
            self._disconnect(f"Connection lost: {error}")
            return []
        if not chunk:
            self._disconnect("JS8Call closed the connection")
            return []
        self._buffer += chunk
        return self._decode_lines()

    def _connect(self) -> bool:
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=0.5)
            self._socket.setblocking(False)
            self.status = f"Connected to {self.host}:{self.port}"
            self.last_error = None
            return True
        except OSError as error:
            self._socket = None
            self.status = "Disconnected"
            self.last_error = str(error)
            return False

    def _decode_lines(self) -> list[Message]:
        messages: list[Message] = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(self._message_from_payload(payload, line.decode("utf-8", "replace")))
        return messages

    def _message_from_payload(self, payload: dict[str, Any], raw: str) -> Message:
        content = payload.get("params", payload.get("message", payload.get("text", raw)))
        if isinstance(content, (dict, list)):
            content = json.dumps(content, sort_keys=True)
        received_at = payload.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(received_at) if isinstance(received_at, str) else None
        except ValueError:
            timestamp = None
        return Message(
            source_application=self.application_name,
            raw_content=str(content),
            received_at=timestamp or datetime.now(timezone.utc),
            fields={key: str(value) for key, value in payload.items()},
        )

    def _disconnect(self, reason: str) -> None:
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._buffer = b""
        self.status = "Disconnected"
        self.last_error = reason

    def close(self) -> None:
        self._disconnect("Closed")
