import re
from dataclasses import replace
from datetime import timedelta, datetime, timezone
from pathlib import Path

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.models import Message


class FLMSGFileAdapter(ApplicationAdapter):
    """Reads newly created FLMSG message files from an ICS message directory."""

    application_name = "FLMSG"
    SUPPORTED_SUFFIXES = {".k2s", ".b2s", ".wrap"}

    def __init__(self, directory: str | Path, lookback: timedelta | None = None) -> None:
        self.directory = Path(directory).expanduser()
        self.status = "Disconnected"
        self.last_error: str | None = None
        self._seen: dict[str, tuple[int, int]] = {}
        self.lookback = lookback

    def read_messages(self) -> list[Message]:
        if not self.directory.exists():
            self.status = "Directory not found"
            return []
        messages: list[Message] = []
        try:
            files = sorted(
                (path for path in self.directory.iterdir() if path.suffix.lower() in self.SUPPORTED_SUFFIXES),
                key=lambda path: path.stat().st_mtime_ns,
            )
            for path in files:
                stat = path.stat()
                if self.lookback and datetime.fromtimestamp(stat.st_mtime, timezone.utc) < datetime.now(timezone.utc) - self.lookback:
                    self._seen[str(path)] = (stat.st_ino, stat.st_mtime_ns)
                    continue
                signature = (stat.st_ino, stat.st_mtime_ns)
                if self._seen.get(str(path)) == signature:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                # FLMSG files carry no reliable receive time; the file's is when it arrived here.
                messages.append(replace(self._parse(path, content),
                                        received_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc)))
                self._seen[str(path)] = signature
        except OSError as error:
            self.status = "Directory unavailable"
            self.last_error = str(error)
            return []
        self.status = "Connected"
        self.last_error = None
        return messages

    def _parse(self, path: Path, content: str) -> Message:
        form_match = re.search(r":mg:\d+\s+CUSTOM_FORM,([^\s]+)", content)
        sender_match = re.search(r":hdr_fm:\d+\s+\n?([^\s]+)", content)
        form_name = form_match.group(1) if form_match else path.suffix.lstrip(".").upper()
        body_match = re.search(r":mg:\d+\s+(.*)", content, re.DOTALL)
        body = body_match.group(1).strip() if body_match else content.strip()
        if body.startswith("[WRAP:"):
            body = re.sub(r"^\[WRAP:[^\]]*\]\s*", "", body, flags=re.DOTALL).strip()
        fields = {
            "type": path.suffix.lstrip(".").upper(),
            "form": form_name,
            "sender": sender_match.group(1) if sender_match else "",
            "filename": path.name,
        }
        return Message(self.application_name, body, fields=fields)

    def close(self) -> None:
        self.status = "Closed"
