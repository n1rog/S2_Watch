from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Message:
    source_application: str
    raw_content: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    form_name: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


# A watch form for this application applies to messages from every monitored application.
ANY_APPLICATION = "All applications"
# Every monitored source: the radio messaging applications, then official/open sources.
APPLICATIONS = ("JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter", "OSINT")


@dataclass(frozen=True)
class WatchForm:
    application: str
    name: str
    match_text: str
    enabled: bool = True
    use_regex: bool = False
    message_type: str = ""
    sender: str = ""

    def matches(self, message: Message) -> bool:
        if not self.enabled:
            return False
        if self.application != ANY_APPLICATION and message.source_application != self.application:
            return False
        if self.message_type and message.fields.get("type", "").casefold() != self.message_type.casefold():
            return False
        message_sender = message.fields.get("sender", message.fields.get("from", ""))
        if self.sender and message_sender.casefold() != self.sender.casefold():
            return False
        if not self.match_text:
            return True
        if self.use_regex:
            import re
            return re.search(self.match_text, message.raw_content, re.IGNORECASE) is not None
        return self.match_text.casefold() in message.raw_content.casefold()
