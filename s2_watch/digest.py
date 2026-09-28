"""Split multi-item digests (weekly briefings, bulletins, news roundups) into items.

A digest such as "MAGNET WEEKLY BRIEFING" carries a dozen unrelated items from several
states. Treated as one report it gets one category, one place and one official
confirmation, each taken from a different item. Split, every item gets its own.

Items start with a state prefix ("MN — …", "MI/WI - …"), a section heading
("CIVIL UNREST: …"), or a bullet/number; other lines continue the previous item. FLMSG
custom forms keep the body in one field with line breaks written as a literal "\\n".
"""

import re
from dataclasses import replace

from s2_watch.models import Message

MIN_ITEMS = 3
MIN_LENGTH = 200
_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
    "OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|PR|GU|VI"
)
_STATE_ITEM = re.compile(rf"^(?:{_STATES})(?:\s*/\s*(?:{_STATES}))*\s*[-—–:]\s*\S")
_SECTION_ITEM = re.compile(r"^([A-Z][A-Z &/]{2,30}):\s+(\S.*)$")
_BULLET_ITEM = re.compile(r"^(?:[-•*▪►]|\d{1,2}[.)])\s+(\S.*)$")
_FORM_FIELD = re.compile(r"^L\d{2},(.*)$")


def _unescape(text: str) -> str:
    return text.replace("\\n", "\n").replace('\\"', '"')


def _digest_body(text: str) -> str:
    if text.startswith("CUSTOM_FORM"):
        # FLMSG custom form: the body is the longest "Lnn," field.
        fields = [match.group(1) for line in text.split("\n") if (match := _FORM_FIELD.match(line))]
        return _unescape(max(fields, key=len)) if fields else ""
    return _unescape(text)


def split_digest(message: Message) -> list[Message] | None:
    """The digest's items as separate messages, or None when the message is not a digest."""
    if len(message.raw_content) < MIN_LENGTH:
        return None
    body = _digest_body(message.raw_content)
    header: list[str] = []
    items: list[tuple[str, list[str]]] = []   # (section heading, lines)
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _SECTION_ITEM.match(line)
        bullet = _BULLET_ITEM.match(line)
        if _STATE_ITEM.match(line):
            items.append(("", [line]))
        elif section and not _STATE_ITEM.match(line):
            items.append((section.group(1).strip().title(), [section.group(2)]))
        elif bullet:
            items.append(("", [bullet.group(1)]))
        elif items:
            items[-1][1].append(line)
        else:
            header.append(line)
    if len(items) < MIN_ITEMS:
        return None
    title = next((line for line in header if len(line) > 8), header[0] if header else "Digest")
    title = re.sub(r"^This is the\s+", "", title, flags=re.IGNORECASE).rstrip(".")[:90]
    parent_id = message.fields.get("message_id") or message.fields.get("filename") or message.received_at.isoformat()
    parts = []
    for index, (section, lines) in enumerate(items, start=1):
        text = " ".join(lines)
        fields = dict(message.fields)
        fields.update({
            "message_id": f"{parent_id}#{index}",
            "digest": title,
            "digest_item": f"{index} of {len(items)}",
        })
        if section:
            fields["section"] = section
        parts.append(replace(message, raw_content=text, fields=fields))
    return parts


def digest_label(message: Message) -> str:
    """ "from “MAGNET WEEKLY BRIEFING …” (item 3 of 15, section Cyber)", or "" for other messages."""
    if not message.fields.get("digest"):
        return ""
    section = f", section {message.fields['section']}" if message.fields.get("section") else ""
    return f"from “{message.fields['digest']}” (item {message.fields.get('digest_item', '?')}{section})"
