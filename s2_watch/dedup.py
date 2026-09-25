"""Recognise the same report arriving more than once.

One report typically reaches S2 Watch several times: MagCommstat stores a StatRep,
JS8SuperSpotter stores the same StatRep plus the raw JS8 frame it came in, and other
stations relay it with their own call signs and relay paths in front. Under that
"envelope" the report text is the same, so reports are matched on the text with the
envelope removed, within ``DUPLICATE_WINDOW`` of each other.
"""

import re
from datetime import timedelta

DUPLICATE_WINDOW = timedelta(hours=24)
# Near-identical texts (e.g. one copy with a trailing note) count as the same report.
SIMILARITY_THRESHOLD = 0.85
MIN_WORDS_FOR_SIMILARITY = 6

_CALL = r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,4}(?:/[A-Z0-9]+)?"
# Leading parts of the envelope, removed repeatedly because relays stack them.
_LEADING = [
    re.compile(rf"^\s*{_CALL}\s*:\s*"),                       # "W8UFO: "  (JS8 sender)
    re.compile(rf"^(?:\s*{_CALL}\s*>\s*)+"),                   # "N1MAG> K7RIE> " relay path
    re.compile(r"^\s*(?:@[A-Z0-9]+\s*)+"),                     # "@MAGNET @MR05 "
    re.compile(rf"^\s*(?:{_CALL}\s+)?MSG\b\s*"),              # "MSG " / "KG5RKW MSG " (directed)
    re.compile(r"^\s*(?:TO:|RELAY\s*//|FWD?:?)\s*"),
]
# Trailing parts, in this order: end marker and checksum first, then sign-offs.
_TRAILING = [
    re.compile(r"\s*(?:♢|\{[^}]*\})\s*$"),                     # JS8 end marker, {&%} checksum
    re.compile(rf"\s*\*DE\*\s*{_CALL}\s*$"),                   # "*DE* KF0PLE"
    re.compile(rf"\s*(?:-\s*)?\b(?:FORWARDED\s+BY|FROM)\s*:?\s*{_CALL}\s*$"),
    re.compile(r"\s*#[A-Z0-9]{4}\s*$"),                       # "#AXJ1" form checksum
]
_ANYWHERE = [
    re.compile(r"@[A-Z0-9]+"),
    re.compile(r",[A-R]{2}[0-9]{2}(?:[A-X]{2})?,\d+,[A-Z0-9]{3},[+0-9]*,"),  # CommStat CSV header
    re.compile(r"♢|\{[^}]*\}"),
]


def report_text(text: str) -> str:
    """The report with its radio envelope removed, upper-cased, punctuation-free."""
    core = text.upper().strip()
    # A relayed message can carry the envelope more than once ("W5TTA: N1MAG> K7RIE ...").
    for _ in range(4):
        before = core
        for pattern in _LEADING:
            core = pattern.sub("", core).strip()
        for pattern in _TRAILING:
            core = pattern.sub("", core).strip()
        if core == before:
            break
    for pattern in _ANYWHERE:
        core = pattern.sub(" ", core)
    core = re.sub(r"[^A-Z0-9 ]", " ", core)
    return " ".join(core.split())


def similarity(first: str, second: str) -> float:
    a, b = set(first.split()), set(second.split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_report(first: str, second: str) -> bool:
    if first == second:
        return bool(first)
    if min(len(first.split()), len(second.split())) < MIN_WORDS_FOR_SIMILARITY:
        return False
    return similarity(first, second) >= SIMILARITY_THRESHOLD
