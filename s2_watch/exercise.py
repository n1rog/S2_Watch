"""Exercise and test traffic, recognised so a drill never passes for a real event.

Only clear markers count ("EXERCISE EXERCISE EXERCISE", "THIS IS A DRILL", "exercise
message", "for exercise purposes only", STARTEX/ENDEX...): ordinary words like "exercise
normal precautions" (State Department), "not exercise or test", "stress test" or "military
drills" are left alone. "Real world" or "no duff" marks a message as real. Official and
news feeds (OSINT) are never exercise traffic.

Test traffic is never news. Exercise traffic is kept off the news lists and briefings,
unless the operator says an exercise is in progress: then it is processed like real
traffic, marked EXERCISE wherever it appears.
"""

from __future__ import annotations

import re
from dataclasses import replace

from s2_watch.analysis.base import AnalysisResult
from s2_watch.models import Message

EXERCISE, TEST = "exercise", "test"
BANNER = "EXERCISE EXERCISE EXERCISE"
# Exercise traffic wears one colour everywhere (rows, markers, badges): purple, which no
# concern level, category or highlight uses, so a drill is seen as a drill at a glance.
COLOUR = "#8e24aa"
TINT_ALPHA = 90          # the row tint: COLOUR at this alpha, readable in light and dark themes

_NOT_REAL = re.compile(r"\b(?:not|no|non)[-\s]+real[-\s]?world\b", re.IGNORECASE)
_REAL = re.compile(r"\breal[-\s]?world\b|\bno[-\s]+duff\b", re.IGNORECASE)
# The word as operators type it under pressure: EXERCIZE, EXCERCISE, EXERSIZE...
_WORD = r"(?:exercise|exercize|excercise|excercize|exersise|exersize|exer|drill)"
_EXERCISE = re.compile(
    r"\b(exercise|exer|drill)\b[\s,.:;!/-]*\b\1\b"                       # EXERCISE EXERCISE (EXERCISE)
    r"|\bthis\s+is\s+(?:only\s+)?(?:an?\s+)?(?:exercise|drill)\b"
    r"|\b(?:exercise|drill)\s+(?:message|msg|traffic|only|inject|scenario|play|participants?)\b"
    r"|\bfor\s+(?:exercise|drill|training)\s+(?:purposes\s+)?only\b"
    r"|\b(?:simulated|notional)\s+(?:event|scenario|traffic|message|emergency|disaster|incident)\b"
    r"|\b(?:startex|endex)\b"
    # A banner: "*** EXERCISE ***", "== EXERCIZE ==", "--- DRILL ---"
    rf"|(?:[*=#~]{{2,}}|-{{3,}})\s*\b{_WORD}\b\s*(?:[*=#~]{{2,}}|-{{3,}})"
    # The word alone on its line (FLMSG forms write line breaks as a literal \n)
    rf"|(?:^|\n|\\n)[^\w\n\\]*\b{_WORD}\b[^\w\n\\]*(?=$|\n|\\n)",
    re.IGNORECASE,
)
_TEST = re.compile(
    r"\b(test)\b[\s,.:;!/-]*\btest\b"                                       # TEST TEST
    r"|\bthis\s+is\s+(?:only\s+)?an?\s+test\b"
    r"|\btest\s+(?:message|msg|traffic|only|transmission)\b",
    re.IGNORECASE,
)
# The message itself starts with the word (after its routing): "EXERCISE: DAM FAILURE...".
_LEADING = re.compile(rf"^\W*(?:({_WORD})\s*[:\-–]|(test)\b)", re.IGNORECASE)


def exercise_kind(message: Message) -> str:
    """"exercise", "test", or "" for real traffic."""
    if message.source_application == "OSINT":
        return ""
    from s2_watch.analysis.ollama import readable_text

    text = message.raw_content
    not_real = bool(_NOT_REAL.search(text))
    if not not_real and _REAL.search(text):
        return ""
    if not_real or _EXERCISE.search(text):
        return EXERCISE
    if _TEST.search(text):
        return TEST
    leading = _LEADING.match(readable_text(message))
    if leading:
        return EXERCISE if leading.group(1) else TEST
    return ""


def mark_exercise(message: Message, result: AnalysisResult, exercise_in_progress: bool) -> AnalysisResult:
    """The analysis as S2 Watch acts on it: test traffic is not news; exercise traffic is news
    only during an exercise, and always labelled."""
    kind = exercise_kind(message)
    if not kind:
        return result
    if kind == TEST:
        summary = result.summary if result.summary.startswith("Test traffic") else f"Test traffic: {result.summary}"
        return replace(result, newsworthy=False, category="routine", summary=summary)
    summary = result.summary if result.summary.upper().startswith("EXERCISE") else f"EXERCISE: {result.summary}"
    if exercise_in_progress:
        return replace(result, summary=summary)
    return replace(result, newsworthy=False, category="routine", summary=summary)
