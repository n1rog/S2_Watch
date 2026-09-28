"""One-line headline summaries: what happened, where, and the key facts.

The offline analyzer only knows which terms matched, so this builds the summary from
the message itself: the matched event phrase (or a named fire or storm), the most
specific location found for it, and facts such as casualties, acreage, containment,
magnitude, temperatures, snowfall, wind, and "until ..." times. For example:

    "MO - EXCESSIVE HEAT WARNING THRU TUES HEAT INDEX 105-110f"
    -> "Excessive heat warning — Missouri · heat index 105–110°F · through Tues"
"""

import re

from s2_watch.analysis.base import AnalysisResult
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.dedup import strip_envelope
from s2_watch.models import Message

MAX_DETAILS = 3
GIST_WORDS = 12

_NAME_WORD = r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
_NAMED_FIRE = re.compile(rf"\b((?:{_NAME_WORD}\s+){{0,2}}{_NAME_WORD})\s+Fire\b")
_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*"
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ZONE = r"(?:[ecmp][sd]t|ak[sd]t|hst|utc|gmt|z)"
# A clock time needs a colon or am/pm/zone, so a bare "9" is not a time.
_CLOCK = rf"\d{{1,2}}(?:(?::\d{{2}})\s*(?:am|pm)?|\s*(?:am|pm)|\d{{2}}\s*z)(?:\s*{_ZONE})?"
_CASUALTIES = re.compile(r"\b(\d[\d,]*)\s+(dead|killed|deaths|injured|hospitali[sz]ed|missing|wounded)\b", re.I)
_NAMED_STORM = re.compile(
    r"\b(Hurricane|Tropical\s+Storm|Tropical\s+Depression|Typhoon|Cyclone)\s+([A-Z][a-z]+)\b", re.IGNORECASE
)
_FACTS = [
    # (pattern, formatter) - formatters get the match and return display text.
    (re.compile(r"\b(?:magnitude|mag\.?)\s*(\d(?:\.\d)?)\b|\bM\s?(\d\.\d)\b", re.I),
     lambda m: f"M{m.group(1) or m.group(2)}"),
    (re.compile(r"\b(\d[\d,. ]*\d|\d)\s*(k\s*)?acres\b", re.I),
     lambda m: f"{m.group(1).replace(' ', ',')}{'k' if m.group(2) else ''} acres"),
    (re.compile(r"\b(\d{1,3})\s*(?:%|pct|percent)\s*contain\w*", re.I),
     lambda m: f"{m.group(1)}% contained"),
    (re.compile(r"\bheat\s+ind(?:ex|ices)\s*(?:of|to|up\s+to|near|at|:)?\s*(\d{2,3})(?:\s*(?:-|–|to)\s*(\d{2,3}))?", re.I),
     lambda m: f"heat index {m.group(1)}" + (f"–{m.group(2)}" if m.group(2) else "") + "°F"),
    (re.compile(r"\bwind\s*chills?\s*(?:of|to|near|:)?\s*(-?\d{1,2})\b", re.I),
     lambda m: f"wind chill {m.group(1)}°F"),
    (re.compile(r"(?<![!#\w.])(\d+(?:\s*-\s*\d+)?)\s*(?:\"|in(?:ch(?:es)?)?\.?)\s+(?:of\s+)?snow\b", re.I),
     lambda m: f"{m.group(1).replace(' ', '')}\" snow"),
    (re.compile(r"(?<![!#\w.])(\d{2,3})\s*mph\b", re.I),
     lambda m: f"{m.group(1)} mph"),
    (re.compile(r"(?<![!#\w.])(-?\d{1,3}(?:\.\d)?)\s*(?:°\s*F?|deg(?:rees?|s)?\b(?:\s*F\b)?|F\b)", re.I),
     lambda m: f"{m.group(1)}°F"),
    (re.compile(r"\b(\d[\d,.]*\s*k?)\s+(?:residents|customers|homes|people)\b[^.]{0,30}\bwithout\s+power", re.I),
     lambda m: f"{m.group(1).strip()} without power"),
    (re.compile(rf"\b(until|thru|through|till?)\s+((?:today|tonight|tomorrow|noon|midnight|{_DAY}|{_MONTH}\s*\d{{0,2}}|{_CLOCK})"
                rf"(?:\s+(?:{_DAY}|{_CLOCK}|tonight|morning|evening))?)(?![a-z])", re.I),
     lambda m: f"{'through' if m.group(1).lower() in ('thru', 'through') else 'until'} {_time_text(m.group(2))}"),
]


def _time_text(text: str) -> str:
    """ "8 PM MST MONDAY" -> "8 PM MST Monday"; "TUES" -> "Tues"."""
    words = []
    for word in text.split():
        if re.fullmatch(rf"(?:am|pm|{_ZONE})", word, re.I) or re.fullmatch(r"\d{4}z", word, re.I):
            words.append(word.upper())
        else:
            words.append(word.capitalize() if word.isupper() else word)
    return " ".join(words)


def _sentence_case(text: str) -> str:
    text = " ".join(text.split())
    if text.isupper() or text.islower():
        text = text.lower()
    return text[:1].upper() + text[1:]


def _event_phrase(body: str, result: AnalysisResult, fact_spans=()) -> str:
    fire = _NAMED_FIRE.search(body)
    if fire and fire.group(1).lower() not in {"the", "a", "brush", "grass", "forest", "wild", "house"}:
        return f"{fire.group(1)} Fire"
    storm = _NAMED_STORM.search(body)
    if storm:
        return f"{' '.join(storm.group(1).split()).title()} {storm.group(2).title()}"
    # Merge the spans of every matched term, e.g. "extreme heat" + "heat warning"
    # -> "EXTREME HEAT WARNING", and keep the longest.
    lowered = body.lower()
    # Terms of the report's own category first ("Government" is not a Civil Unrest headline).
    own_terms = [
        term for rule in RuleBasedNewsAnalyzer.DEFAULT_RULES if rule.category == result.category
        for term in rule.matches(lowered)
    ]
    def spans_of(terms):
        found = []
        for term in terms:
            start = lowered.find(term.lower())
            while start >= 0:
                found.append([start, start + len(term)])
                start = lowered.find(term.lower(), start + 1)
        return found

    def distinct(spans):
        # An event phrase that is not also one of the facts ("heat index 100", "2 injured").
        return [span for span in spans if not any(span[0] < end and start < span[1] for start, end in fact_spans)]

    own, every = spans_of(own_terms), spans_of(result.matched_terms)
    spans = sorted(distinct(own) or distinct(every) or own or every)
    if not spans:
        return result.category
    merged = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    start, end = max(merged, key=lambda span: span[1] - span[0])
    # Extend to a following product word: "heat" + " WARNING", "flood" + " WATCH".
    tail = re.match(r"\s+(warnings?|watch(?:es)?|advisor(?:y|ies)|emergency|alert|threats?)\b", body[end:], re.I)
    if tail:
        end += tail.end()
    return _sentence_case(body[start:end])


def _facts(body: str) -> tuple[list[str], list[tuple[int, int]]]:
    found: list[str] = []
    taken: list[tuple[int, int]] = []
    casualties = list(_CASUALTIES.finditer(body))
    if casualties:
        found.append(", ".join(f"{m.group(1)} {m.group(2).lower()}" for m in casualties[:3]))
        taken.extend(m.span() for m in casualties)
    for pattern, formatter in _FACTS:
        for match in pattern.finditer(body):
            # A number already used by an earlier fact (e.g. the heat index) is not reused.
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            fact = formatter(match)
            if fact not in found:
                found.append(fact)
                taken.append(match.span())
            break
        if len(found) >= MAX_DETAILS:
            break
    return found, taken


def _gist(body: str) -> str:
    words = body.split()
    text = " ".join(words[:GIST_WORDS]).rstrip(",;:-")
    return _sentence_case(text) + ("…" if len(words) > GIST_WORDS else "")


def summarize(message: Message, result: AnalysisResult, locations=()) -> str:
    """A headline for the Newsworthiness table and alerts, instead of the full message."""
    body = re.sub(r"https?://\S+|www\.\S+", "", strip_envelope(message.raw_content)).strip(" ,;:-")
    advisory = re.match(r"State Dept travel advisory: (.+?) - (Level \d: [^.]+)", body)
    if advisory:
        return f"Travel advisory — {advisory.group(1)} · {advisory.group(2)}"
    if not body:
        return result.category if result.newsworthy else "Routine traffic"
    if not result.newsworthy:
        return _gist(body)
    facts, fact_spans = _facts(body)
    event = _event_phrase(body, result, fact_spans)
    # A fact that only repeats the headline ('3" of snow' / '3" snow') is dropped.
    event_key = re.sub(r"[^a-z0-9]", "", event.lower())
    facts = [fact for fact in facts if re.sub(r"[^a-z0-9]", "", fact.lower().replace("of", "")) not in event_key.replace("of", "")]
    headline = event
    where = locations[0] if locations else None
    if where is not None:
        label = where.label.replace("StatRep area, grid", "grid").replace("Grid ", "grid ")
        headline += f" — {label}"
    return " · ".join([headline, *facts])


def is_placeholder_summary(message: Message, summary: str) -> bool:
    """True for the offline analyzer's summaries, which repeat the message text."""
    text = " ".join(message.raw_content.split())
    return not summary.strip() or text[:60] in " ".join(summary.split())
