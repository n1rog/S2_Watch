"""How concerning an event is: High, Elevated, Guarded or Low, by fixed rules, with the reasons.

Not the AI, and not a percentage: a level the analyst can check from the reasons shown
("Elevated — high urgency · 1 source, unconfirmed · in focus region 6"). From what S2 Watch
already knows of an event:

    High      high urgency AND confirmed (an official source, OR 2+ independent sources one of
              them a radio station) AND in a focus region (or no focus regions are set)
    Elevated  high urgency otherwise (unconfirmed, or outside the focus regions) from radio, an
              official source or 2+ outlets, OR medium urgency confirmed and escalating
    Guarded   medium urgency, a single news story (one outlet), OR a single-source radio report
              not yet confirmed
    Low       everything else

News coverage alone doesn't confirm: several outlets running one story is wide reporting, not
corroboration, so a news-only event reaches Elevated at most.

A quiet event (no report for the quiet period) steps down one level; a closed one is Low. An
accepted assessment's trend shows in the reasons, and escalating can raise medium to Elevated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

LEVELS = ("High", "Elevated", "Guarded", "Low")
COLOURS = {"High": "#c62828", "Elevated": "#ef6c00", "Guarded": "#f9a825", "Low": "#78909c"}
TREND_MARKS = {"escalating": "▲", "stable": "▬", "winding down": "▼", "unclear": "?"}


@dataclass(frozen=True)
class Concern:
    level: str
    reasons: tuple[str, ...]

    @property
    def rank(self) -> int:
        return len(LEVELS) - LEVELS.index(self.level)      # High 4 ... Low 1

    def text(self) -> str:
        return f"{self.level} — {' · '.join(self.reasons)}" if self.reasons else self.level


def concern(event, now: datetime, focus_regions=(), trend: str = "") -> Concern:
    """The event's concern level and why (``trend``: its accepted assessment's, if any)."""
    from s2_watch.focus import event_in_focus, has_focus

    reasons = [f"{event.urgency} urgency"]
    official = bool(event.official)
    sources = max(1, int(getattr(event, "independent_sources", 0) or 0))
    if official:
        reasons.append(f"confirmed by {event.official}")
    reasons.append(f"{sources} independent source{'s' if sources != 1 else ''}" if sources > 1
                   else "1 source" + ("" if official else ", unconfirmed"))
    radio = any(source != "OSINT" for source in event.sources)
    confirmed = official or (sources >= 2 and radio)
    if sources >= 2 and not radio and not official:
        reasons.append("news only")
    focused = has_focus(focus_regions)
    in_focus = not focused or event_in_focus(event, focus_regions)
    if focused:
        reasons.append(f"in focus region {event.region}" if in_focus and event.region
                       else "in your focus area" if in_focus else "outside your focus regions")
    if trend:
        reasons.append(f"trend {TREND_MARKS.get(trend, '?')} {trend}")
    escalating = trend == "escalating"
    if event.urgency == "high" and confirmed and in_focus:
        level = "High"
    elif (event.urgency == "high" and (radio or official or sources >= 2)) or \
            (event.urgency == "medium" and confirmed and escalating):
        level = "Elevated"
    elif event.urgency in ("high", "medium") or event.single_source(now):
        level = "Guarded"     # one news story, however urgent its words, is one outlet's account
        if event.single_source(now) and event.urgency != "medium":
            reasons.append("single source, not yet confirmed")
    else:
        level = "Low"
    if event.closed_at is not None:
        return Concern("Low", tuple(reasons + ["closed"]))
    if event.status(now) == "quiet":
        level = LEVELS[min(len(LEVELS) - 1, LEVELS.index(level) + 1)]
        reasons.append(event.status_text(now).lower())
    return Concern(level, tuple(reasons))


def situation_summary(events, now: datetime, focus_regions=(), trends: dict | None = None) -> str:
    """HTML for Needs Review's side panel: open events counted by concern level."""
    trends = trends or {}
    counts = {level: 0 for level in LEVELS}
    for event in events:
        counts[concern(event, now, focus_regions, trends.get(event.event_id, "")).level] += 1
    rows = "".join(f"<tr><td><span style='color:{COLOURS[level]}'>●</span> {level}</td>"
                   f"<td align='right'><b>{counts[level]}</b></td></tr>" for level in LEVELS)
    return (f"<table cellspacing='4'>{rows}</table>"
            f"<p style='color:gray'>{len(events)} open events in the watch period.</p>")
