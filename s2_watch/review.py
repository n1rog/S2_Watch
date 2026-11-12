"""Needs Review: everything waiting on the operator, in one list.

New high-urgency events (and events a desktop alert was raised for), Ollama assessments
waiting to be accepted, briefings drafted at shift change, single-source radio reports no one
has confirmed, possible misses, and drafts not yet sent. Each item goes when it is acted on
(opened, accepted, sent) or hidden; hidden ones are remembered (``store.review_done``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

NEW_FOR = timedelta(hours=24)          # a high-urgency event is "new" this long
UNSENT_FOR = timedelta(days=7)         # an unsent draft is listed this long

# kind -> (label, the primary action, the secondary action)
KINDS = {
    "new": ("New event", "Open event", "Hide"),
    "assessment": ("AI assessment ready", "Review assessment", "Hide"),
    "briefing": ("Shift briefing ready", "Review briefing", "Hide"),
    "single": ("Single source", "Open event", "Hide"),
    "miss": ("Possible miss", "Add as report…", "Not news"),
    "draft": ("Unsent draft", "Open draft", "Hide"),
}


@dataclass
class ReviewItem:
    key: str              # what review_done remembers: "new:12", "draft:3", "miss:<key>"
    kind: str
    when: datetime
    title: str
    detail: str = ""
    target: object = None  # an event id, a draft id or a miss key
    payload: object = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return KINDS[self.kind][0]


def review_items(now: datetime, events, done: set[str], *, alerted=(), proposals=(), event_lookup=None,
                 misses=(), drafts=(), concern_of=None) -> list[ReviewItem]:
    """The items waiting, newest first. ``events``: recent EventSummary objects; ``alerted``:
    event ids a desktop alert was raised for; ``proposals``: event ids with an assessment to
    review; ``event_lookup(ids)``: their summaries; ``misses``: (key, message, result);
    ``drafts``: store.list_drafts(); ``concern_of(event)``: its Concern (s2_watch.concern).
    A "new event" is one at High concern first heard in the last day, or an alerted one at
    Elevated or above: high urgency alone would list every news story about a shooting."""
    if concern_of is None:
        from s2_watch.concern import concern

        def concern_of(event):
            return concern(event, now)
    items: list[ReviewItem] = []
    alerted = set(alerted)
    by_id = {event.event_id: event for event in events}
    for event in events:
        if event.closed_at is not None:
            continue
        sources = f"{event.reports} report{'s' if event.reports != 1 else ''} · {', '.join(event.sources)}"
        level = concern_of(event)
        if (level.level == "High" and now - event.first_seen <= NEW_FOR) or \
                (event.event_id in alerted and level.level in ("High", "Elevated")):
            items.append(ReviewItem(f"new:{event.event_id}", "new", event.last_seen, event.title,
                                    level.text(), event.event_id))
        elif event.single_source(now):
            items.append(ReviewItem(f"single:{event.event_id}", "single", event.last_seen, event.title,
                                    f"one station, no official confirmation · {sources}", event.event_id))
    missing = [event_id for event_id in proposals if event_id not in by_id]
    if missing and event_lookup is not None:
        by_id.update({event.event_id: event for event in event_lookup(missing)})
    for event_id in proposals:
        event = by_id.get(event_id)
        if event is not None:
            items.append(ReviewItem(f"assessment:{event_id}", "assessment", event.last_seen, event.title,
                                    "Ollama's assessment is waiting to be accepted, edited or dismissed", event_id))
    for key, message, result in misses:
        when = message.received_at
        items.append(ReviewItem(f"miss:{key}", "miss", when, result.summary,
                                f"{message.source_application} · {result.category} · {result.urgency}", key))
    for draft in drafts:
        if draft["sent_at"]:
            continue
        updated = datetime.fromisoformat(draft["updated_at"])
        if draft["origin"] == "scheduled":
            items.append(ReviewItem(f"draft:{draft['id']}", "briefing", updated, draft["subject"],
                                    f"drafted by S2 Watch at {draft['dtg']}: review and send", draft["id"]))
        elif now - updated <= UNSENT_FOR:
            kind = "briefing" if draft["kind"] == "briefing" else "report"
            items.append(ReviewItem(f"draft:{draft['id']}", "draft", updated, draft["subject"],
                                    f"{kind} {draft['dtg']}, saved but not marked sent", draft["id"]))
    return sorted((item for item in items if item.key not in done), key=lambda item: item.when, reverse=True)
