"""What period a shift briefing covers, and when S2 Watch drafts one by itself.

Every "Draft briefing" (the Reports menu, the Map and Events tabs, Needs Review, the Reports
tab) asks the same question first: since the last briefing (the default), this shift, the
last 12 or 24 hours, the Events tab's period and filters, or a number of hours. A briefing
schedule (UTC times, e.g. 0545 and 1745) drafts one at each shift change for review; nothing
is ever sent by itself.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

BRIEFING_TIMES_SETTING = "briefing.auto_times"     # "0545 1745" (UTC), empty = off
LAST_SCHEDULED_SETTING = "briefing.auto_last"      # the last schedule time drafted (ISO)
LAST_BRIEFING_SETTING = "briefing.last"            # when a briefing was last marked sent
DEFAULT_SHIFTS = ("0600", "1800")                  # "This shift" without a schedule: 12 h shifts
SCHEDULE_GRACE = timedelta(minutes=30)             # a scheduled time missed by more (S2 Watch was off) is skipped
DEFAULT_HOURS = 24                                 # "since the last briefing" before the first one

PERIODS = (
    ("since_last", "Since the last briefing"),
    ("shift", "This shift"),
    ("12", "Last 12 hours"),
    ("24", "Last 24 hours"),
    ("events_tab", "The Events tab's period and filters"),
    ("custom", "Custom number of hours"),
)


def briefing_times(text: str) -> list[str]:
    """UTC times written as HHMM (or HH:MM), sorted, each once: "1745, 05:45" -> ["0545", "1745"]."""
    times = set()
    for hours, minutes in re.findall(r"\b([01]?\d|2[0-3]):?([0-5]\d)\b", text or ""):
        times.add(f"{int(hours):02d}{minutes}")
    return sorted(times)


def _at(day: datetime, hhmm: str) -> datetime:
    return day.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=0, microsecond=0)


def latest_time(now: datetime, times) -> datetime | None:
    """The latest of the daily ``times`` at or before ``now`` (yesterday's if none today)."""
    now = now.astimezone(timezone.utc)
    candidates = [_at(now - timedelta(days=days), hhmm) for days in (0, 1) for hhmm in times]
    past = [when for when in candidates if when <= now]
    return max(past) if past else None


def shift_start(now: datetime, times=()) -> datetime:
    return latest_time(now, times or DEFAULT_SHIFTS) or now - timedelta(hours=12)


def last_briefing(store) -> datetime | None:
    value = store.get_setting(LAST_BRIEFING_SETTING, "")
    try:
        when = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return when if when is None or when.tzinfo else when.replace(tzinfo=timezone.utc)


def period_start(key: str, now: datetime, store, custom_hours: int = 6) -> datetime:
    """When the period ``key`` (one of PERIODS; not "events_tab") starts."""
    if key == "since_last":
        return last_briefing(store) or now - timedelta(hours=DEFAULT_HOURS)
    if key == "shift":
        return shift_start(now, briefing_times(store.get_setting(BRIEFING_TIMES_SETTING, "")))
    if key == "custom":
        return now - timedelta(hours=max(1, int(custom_hours)))
    return now - timedelta(hours=int(key))


def hours_between(start: datetime, now: datetime) -> int:
    return max(1, -(-int((now - start).total_seconds()) // 3600))     # rounded up


def due_briefing(now: datetime, store) -> datetime | None:
    """The scheduled time a briefing is due for now (not yet drafted, missed by less than
    SCHEDULE_GRACE), or None."""
    times = briefing_times(store.get_setting(BRIEFING_TIMES_SETTING, ""))
    slot = latest_time(now, times) if times else None
    if slot is None or now - slot > SCHEDULE_GRACE:
        return None
    return None if store.get_setting(LAST_SCHEDULED_SETTING, "") == slot.isoformat() else slot


def describe(start: datetime, now: datetime, format_time=None) -> str:
    """ "since 11 Nov 1745Z (14 h)" """
    shown = format_time(start) if format_time else start.astimezone(timezone.utc).strftime("%d %b %H%MZ")
    return f"since {shown} ({hours_between(start, now)} h)"
