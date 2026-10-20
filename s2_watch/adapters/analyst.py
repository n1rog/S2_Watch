"""Intelligence the operator entered (Reports → Add Intelligence Entry), delivered like an
OSINT item of type "ANALYST ENTRY". Every entry in the review window is read again when the
sources are, so entries appear, move and disappear with the rest of the watch period.
"""

from __future__ import annotations

from datetime import datetime

from s2_watch.models import Message

APPLICATION = "OSINT"
ENTRY_TYPE = "ANALYST ENTRY"


def entry_message(entry: dict) -> Message:
    fields = {
        "type": ENTRY_TYPE, "sender": entry["source"], "message_id": f"analyst:{entry['id']}",
        "source": "S2 analyst entry", "handling": entry["handling"], "category": entry["category"],
        "urgency": entry["urgency"], "location": entry["place"], "official": "1" if entry["official"] else "",
    }
    text = entry["text"] + (f" ({entry['place']})" if entry["place"] and entry["place"] not in entry["text"] else "")
    return Message(APPLICATION, text, received_at=datetime.fromisoformat(entry["happened_at"]), fields=fields)


class AnalystAdapter:
    """Hands over the entries once each; never skipped for want of an OSINT watch form."""

    application_name = None
    status = "Connected"

    def __init__(self, entries: list[dict]) -> None:
        self._pending = [entry_message(entry) for entry in sorted(entries, key=lambda e: e["happened_at"])]

    def read_messages(self) -> list[Message]:
        messages, self._pending = self._pending, []
        return messages

    def close(self) -> None:
        pass
