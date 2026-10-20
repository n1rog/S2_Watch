"""When a radio report says something happened ("AT 1400Z", "141400Z OCT", "2:15 PM CDT"),
which can be well before the message was heard (relays, store-and-forward).

Only times the message states count; forecast wording ("UNTIL 1800Z", "EXPECTED AT",
"VALID THROUGH") doesn't. A time of day alone is taken as the latest such time not after
the message was heard, and only within a day of it (a little after midnight for a time
just before it); a full date-time group may be up to a month old. Official and news items
(OSINT) carry their own times and aren't read.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

MONTHS = {name: number for number, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
ZONES = {"Z": 0, "UTC": 0, "GMT": 0, "ZULU": 0, "EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6,
         "MST": -7, "PDT": -7, "PST": -8, "AKDT": -8, "AKST": -9, "HST": -10}
SLACK = timedelta(minutes=5)          # clocks differ a little

# 261016-0544Z (MAGNET / MagCommstat date-time stamp)
_STAMP = re.compile(r"\b(\d{2})(\d{2})(\d{2})-([01]\d|2[0-3])([0-5]\d)Z\b")
# 141400Z or 141400Z OCT 26 (military date-time group)
_DTG = re.compile(r"\b(0[1-9]|[12]\d|3[01])([01]\d|2[0-3])([0-5]\d)Z(?:\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{2,4}))?)?\b")
# 1400Z, 14:00 UTC, 2:15 PM CDT, 0930 EDT
_TIME = re.compile(
    r"\b(?:([01]?\d|2[0-3]):?([0-5]\d)\s*(AM|PM)?|(1[0-2]|0?[1-9])\s*(AM|PM))\s*(" + "|".join(ZONES) + r")\b")
_FORECAST = re.compile(r"\b(?:UNTIL|TIL|THROUGH|THRU|EXPIRES?|EXPIRING|VALID|EXPECTED|FORECAST|BY|AFTER|"
                       r"STARTING|BEGINNING|NEXT|SCHEDULED|NET|CHECK-?INS?)\s+(?:AT\s+)?$")


def _forecast(text: str, start: int) -> bool:
    return bool(_FORECAST.search(text[max(0, start - 30):start]))


def event_time(text: str, heard: datetime) -> datetime | None:
    """The time the text says something happened, or None."""
    heard = heard if heard.tzinfo else heard.replace(tzinfo=timezone.utc)
    heard = heard.astimezone(timezone.utc)
    upper = text.upper()
    for match in _STAMP.finditer(upper):
        if _forecast(upper, match.start()):
            continue
        year, month, day, hour, minute = (int(group) for group in match.groups())
        try:
            when = datetime(2000 + year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        if heard - timedelta(days=31) <= when <= heard + SLACK:
            return when
    for match in _DTG.finditer(upper):
        if _forecast(upper, match.start()):
            continue
        day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3))
        month = MONTHS.get(match.group(4) or "", heard.month)
        year = int(match.group(5)) if match.group(5) else heard.year
        year = year + 2000 if year < 100 else year
        try:
            when = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        if when > heard + SLACK and not match.group(4):     # a day number from last month
            month, year = (heard.month - 1, heard.year) if heard.month > 1 else (12, heard.year - 1)
            try:
                when = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            except ValueError:
                continue
        if heard - timedelta(days=31) <= when <= heard + SLACK:
            return when
    for match in _TIME.finditer(upper):
        if _forecast(upper, match.start()):
            continue
        if match.group(4):                                   # 2 PM CDT
            hour, minute, half = int(match.group(4)), 0, match.group(5)
        else:                                                # 1400Z, 14:00 UTC, 2:15 PM CDT
            hour, minute, half = int(match.group(1)), int(match.group(2)), match.group(3)
            if not half and ":" not in match.group(0) and len(match.group(1) + match.group(2)) < 4:
                continue                                     # "930Z" is ambiguous; "0930Z" isn't
        if half:
            if hour > 12:
                continue
            hour = hour % 12 + (12 if half == "PM" else 0)
        offset = timedelta(hours=ZONES[match.group(6)])
        local_heard = heard + offset
        when = local_heard.replace(hour=hour, minute=minute, second=0, microsecond=0) - offset
        if when > heard + SLACK:
            when -= timedelta(days=1)
            if heard - when > timedelta(hours=6):            # "1800Z" said at 1500Z: not a past time
                continue
        if heard - when <= timedelta(days=1):
            return when
    return None
