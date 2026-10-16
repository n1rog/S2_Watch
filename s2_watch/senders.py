"""Sender track records: how a radio station's reports have held up, as a suggested grade.

A station's newsworthy radio reports are judged by what happened next: confirmed by an
official source, or marked Of interest / Follow-up by the operator, counts for it; marked
Ignored counts against it; anything else isn't judged yet. With enough judged reports the
share that held up suggests an Admiralty reliability grade (B usually reliable down to E
unreliable; A is left to official sources). Fewer than MIN_JUDGED judged reports: F,
cannot be judged. The grade is a suggestion for the analyst, shown with its counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

MIN_JUDGED = 5
GRADES = ((0.85, "B", "usually reliable"), (0.65, "C", "fairly reliable"), (0.4, "D", "not usually reliable"),
          (0.0, "E", "unreliable"))
GRADE_WORDS = {"F": "cannot be judged", **{grade: words for _share, grade, words in GRADES}}
_JS8_FROM = re.compile(r'"FROM":\s*"([A-Za-z0-9/]+)"')


def callsign(source: str, sender: str, text: str = "") -> str:
    """The station that sent a radio report (upper case), or "" when unknown."""
    sender = (sender or "").strip()
    if not sender and source == "JS8Call":   # JS8Call's record carries the callsign
        match = _JS8_FROM.search(text or "")
        sender = match.group(1) if match else ""
    return sender.upper()


@dataclass
class TrackRecord:
    callsign: str
    reports: int = 0
    confirmed: int = 0     # an official source confirmed its event
    useful: int = 0        # marked Of interest or Follow-up (and not confirmed)
    ignored: int = 0       # marked Ignored (and not confirmed)
    last_heard: datetime | None = None

    @property
    def judged(self) -> int:
        return self.confirmed + self.useful + self.ignored

    @property
    def held_up(self) -> int:
        return self.confirmed + self.useful

    @property
    def grade(self) -> str:
        if self.judged < MIN_JUDGED:
            return "F"
        share = self.held_up / self.judged
        return next(grade for threshold, grade, _words in GRADES if share >= threshold)

    @property
    def explanation(self) -> str:
        if self.judged < MIN_JUDGED:
            return f"too few judged reports ({self.judged} of {MIN_JUDGED} needed)"
        return f"{self.held_up} of {self.judged} judged reports held up"

    def add(self, seen: datetime, confirmed: bool, status: str) -> None:
        self.reports += 1
        if confirmed:
            self.confirmed += 1
        elif status in ("interest", "followup"):
            self.useful += 1
        elif status == "ignored":
            self.ignored += 1
        if self.last_heard is None or seen > self.last_heard:
            self.last_heard = seen
