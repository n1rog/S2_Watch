from pathlib import Path

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.models import Message


class TextLogAdapter(ApplicationAdapter):
    """Tails a line-oriented log without requiring the source app to be visible."""

    def __init__(self, path: str | Path, application_name: str = "JS8Call") -> None:
        self.path = Path(path).expanduser()
        self.application_name = application_name
        self._position = 0
        self._inode: int | None = None

    def read_messages(self) -> list[Message]:
        if not self.path.exists():
            return []
        stat = self.path.stat()
        if self._inode != stat.st_ino or stat.st_size < self._position:
            self._position = 0
            self._inode = stat.st_ino
        with self.path.open("r", encoding="utf-8", errors="replace") as log:
            log.seek(self._position)
            lines = log.readlines()
            self._position = log.tell()
        return [
            Message(source_application=self.application_name, raw_content=line.rstrip("\r\n"))
            for line in lines
            if line.strip()
        ]
