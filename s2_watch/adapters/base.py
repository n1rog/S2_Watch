from abc import ABC, abstractmethod

from s2_watch.models import Message


class ApplicationAdapter(ABC):
    application_name: str

    @abstractmethod
    def read_messages(self) -> list[Message]:
        """Return messages newly available since the previous read."""

    def close(self) -> None:
        """Release resources held by the adapter."""
