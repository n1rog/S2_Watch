"""An Ollama assessment of a whole event: what its reports add up to.

The event's first report and its latest few (numbered as in the event panel), its official
confirmation and its BACKGROUND facts go to the local model with the MAGNET S2 analytical rules. It returns the
analytical layer of a MAGNET S2 report: a suggested BLUF, the trend, where reports agree and
disagree, COMMENTS / ASSESSMENT paragraphs in measured language, a probability table,
intelligence gaps, mitigation recommendations and MAGNET guidance. Every statement must
come from the reports and cite them by number ([3]); citations of reports that don't exist
are dropped. It is a suggestion for the analyst: shown apart from the facts, saved with the
number of reports it read, and out of date once a new report arrives. It never changes an
event or a report.

The model's latest result is a proposal. The analyst reviews it, edits it and accepts it; the
accepted assessment belongs to the event (with who accepted it and when, every earlier
version kept) and is what drafts and briefings use. An analyst can also write one from scratch.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from s2_watch.analysis.ollama import OllamaError, ollama_generate, readable_text
from s2_watch.models import Message

TRENDS = ("escalating", "stable", "winding down", "unclear")
LEVELS = ("HIGH", "MODERATE", "LOW")
LATEST = 6                  # the newest reports the model reads, with the first one
MAX_TEXT = 500              # characters of each report's text
STOP = "=== END ==="
# Small local models wander and repeat: a length cap, a repeat penalty and an end marker.
OPTIONS = {"num_predict": 900, "repeat_penalty": 1.15, "stop": [STOP]}
SECTIONS = ("BLUF", "TREND", "AGREEMENT", "ASSESSMENT", "PROBABILITY", "GAPS", "MITIGATION", "GUIDANCE")

PROMPT = """You are helping a MAGNET S2 (amateur-radio emergency network intelligence) analyst.
Assess ONE event using ONLY the numbered reports and background below.
Rules:
- No facts, numbers, places, forecasts or impacts that are not in the reports. If the reports don't say it, don't.
- Cite the reports each statement rests on by number in brackets, e.g. [3] or [1, 4].
- Measured words: assessed, likely, indicates, suggests. Short lines. Never repeat a line.
- MAGNET guidance is for amateur-radio operators. Do NOT mention PACE unless a report says communications
  failed (outages, repeaters or networks down).

Event: {title}
Category: {category} · Place: {place} · Urgency: {urgency} · Official confirmation: {official}
The event has {count} reports. The current situation is report [{latest}]; judge the trend by comparing
the first report with the latest ones.

Background (facts):
{background}

{reports}

Write exactly these sections, each heading on its own line, then the end marker:
BLUF: 2-3 sentences, the current situation first.
TREND: one of escalating, stable, winding down, unclear; then why, with citations.
AGREEMENT: one sentence: do the reports agree or contradict each other? Cite them.
ASSESSMENT: one or two short paragraphs.
PROBABILITY: one to three lines. Each line names a specific possible outcome drawn from the reports, then "|",
then HIGH, MODERATE or LOW. Example form (use your own outcome): <outcome> | MODERATE
GAPS: up to four lines starting with "- ": questions the reports leave unanswered.
MITIGATION: up to three lines starting with "- ": actions tied to the threats in the reports.
GUIDANCE: up to three lines starting with "- ": actions for MAGNET amateur-radio operators.
{stop}"""

_CITATION = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")


@dataclass
class EventAssessment:
    bluf: str = ""
    trend: str = "unclear"
    trend_reason: str = ""
    agreement: str = ""
    assessment: list[str] = field(default_factory=list)
    probabilities: list[tuple[str, str]] = field(default_factory=list)   # (statement, level)
    gaps: list[str] = field(default_factory=list)
    mitigation: list[str] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)
    reports: int = 0               # how many reports it read (newer ones make it out of date)
    model: str = ""
    created_at: str = ""
    accepted_by: str = ""          # the analyst who accepted it ("" while it's only a proposal)
    accepted_at: str = ""
    origin: str = "ollama"         # "ollama" (a model's draft) or "analyst" (written by the analyst)

    @property
    def credit(self) -> str:
        """"Ollama-drafted, reviewed by N1ROG" or "written by N1ROG"."""
        who = self.accepted_by or "the analyst"
        return f"written by {who}" if self.origin == "analyst" else f"Ollama-drafted ({self.model}), reviewed by {who}"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "EventAssessment | None":
        try:
            data = json.loads(text)
            data["probabilities"] = [tuple(pair) for pair in data.get("probabilities", [])]
            return cls(**data)
        except (TypeError, ValueError):
            return None


def selected(reports: list[dict]) -> list[tuple[int, dict]]:
    """What the model reads, numbered as in the event panel: the first report and the latest
    LATEST (a small model loses track of a long run of similar reports)."""
    numbered = list(enumerate(reports, 1))
    if len(numbered) <= LATEST + 1:
        return numbered
    return numbered[:1] + numbered[-LATEST:]


def _line(number: int, report: dict) -> str:
    text = " ".join(readable_text(Message(report["source"], report.get("text") or "")).split())[:MAX_TEXT]
    sender = report.get("sender") or ""
    who = (sender or "open source") if report["source"] == "OSINT" else \
        f"{report['source']} ({sender})" if sender else report["source"]
    place = report.get("state") or ""
    confirmed = f" — confirmed: {report['corroboration']}" if report.get("corroboration") else ""
    return f"[{number}] {_zulu(report['first_seen'])} — {who}{' — ' + place if place else ''}: {text}{confirmed}"


def event_brief(event, reports: list[dict], background: list[str] | None = None) -> str:
    """The prompt for one event (an EventSummary) and its reports (in the order heard)."""
    chosen = selected(reports)
    if len(chosen) < len(reports):
        skipped = chosen[1][0] - 2
        lines = ["FIRST REPORT:", _line(*chosen[0]), f"({skipped} reports in between not shown)",
                 "LATEST REPORTS, oldest to newest (the last one is the current situation):"]
        lines += [_line(*item) for item in chosen[1:]]
    else:
        lines = ["REPORTS, oldest to newest (the last one is the current situation):"]
        lines += [_line(*item) for item in chosen]
    place = ", ".join(part for part in (event.state, f"MARS Region {event.region}" if event.region else "") if part)
    facts = [line for line in (background or []) if not line.startswith("(")]
    return PROMPT.format(title=event.title, category=event.category, place=place or "unknown",
                         urgency=event.urgency, official=event.official or "none yet", count=len(reports),
                         latest=len(reports), background="\n".join(f"- {line}" for line in facts) or "- none",
                         reports="\n".join(lines), stop=STOP)


def _zulu(value) -> str:
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return f"{when.astimezone(timezone.utc):%d%H%MZ %b}"


def _cited(text: str, count: int) -> str:
    """Keep citations of reports that exist; drop the rest."""
    def keep(match):
        numbers = []
        for part in re.split(r"\s*,\s*", match.group(1)):
            ends = [int(n) for n in re.split(r"\s*[–-]\s*", part) if n]
            numbers += list(range(ends[0], ends[-1] + 1)) if len(ends) == 2 and ends[0] <= ends[1] else ends
        valid = sorted({n for n in numbers if 1 <= n <= count})
        return f"[{', '.join(map(str, valid))}]" if valid else ""
    text = re.sub(r"\]\s*,?\s*\[", ", ", text)                    # "[1], [2]" -> "[1, 2]"
    return re.sub(r"\s+([.,;])", r"\1", _CITATION.sub(keep, text)).strip()


_HEADING = re.compile(r"^\W*(" + "|".join(SECTIONS) + r")\W*?(?::|$)\s*(.*)$", re.I)


def _sections(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current = None
    for raw in text.replace(STOP, "").splitlines():
        line = raw.strip().strip("*").strip()
        match = _HEADING.match(line)
        if match and match.group(1).upper() not in found:
            current = match.group(1).upper()
            rest = match.group(2).strip().strip("*").strip()          # "**BLUF:** text"
            found[current] = [rest] if rest else []
        elif current and line:
            found[current].append(line)
    return found


def _items(lines: list[str], limit: int) -> list[str]:
    items = []
    for line in lines:
        item = line.lstrip("-•*0123456789.) ").strip()
        if item and item.lower() not in {existing.lower() for existing in items}:
            items.append(item)
    return items[:limit]


def _probability(line: str) -> tuple[str, str] | None:
    """ "what may happen | HIGH", "HIGH: what may happen" or "what may happen (LOW)"."""
    level = next((name for name in LEVELS if re.search(rf"\b{name}\b", line.upper())), "")
    if not level:
        return None
    statement = re.sub(rf"\b{level}\b", "", line, flags=re.I)
    statement = re.sub(r"(?i)\bwhat may happen next\b|<outcome>|\boutcome\b\s*:?", "", statement)
    statement = statement.replace("|", " ").strip(" -:()•*<>")
    statement = " ".join(statement.split())
    return (statement, level) if len(statement) > 3 else None


def parse_assessment(text: str, count: int, model: str = "") -> EventAssessment:
    """The model's reply as an EventAssessment; raises OllamaError when it isn't usable."""
    found = _sections(text)
    cite = lambda value: _cited(value, count)     # noqa: E731
    trend_text = " ".join(found.get("TREND", [])).lower()
    trend = next((name for name in TRENDS if trend_text.startswith(name) or f" {name}" in trend_text[:30]), "unclear")
    why = re.sub(r"^\s*(?:" + "|".join(TRENDS) + r")[\s:.,;—–-]*", "", " ".join(found.get("TREND", [])), flags=re.I)
    why = re.sub(r"\b(?:why|because)\s*:\s*", "", why, flags=re.I).strip()
    probabilities = []
    for line in found.get("PROBABILITY", []):
        pair = _probability(line)
        if pair and pair[0].lower() not in {p[0].lower() for p in probabilities}:
            probabilities.append((cite(pair[0]), pair[1]))
    agreement = cite(" ".join(found.get("AGREEMENT", [])))
    result = EventAssessment(
        bluf=cite(re.sub(r"^(?:current situation)\s*:\s*", "", " ".join(
            line.lstrip("-•* ").strip() for line in found.get("BLUF", [])), flags=re.I)),
        trend=trend,
        trend_reason=cite(why),
        agreement=agreement,
        assessment=[cite(p) for p in _items(found.get("ASSESSMENT", []), 4)],
        probabilities=probabilities[:3],
        gaps=[cite(item) for item in _items(found.get("GAPS", []), 4)],
        mitigation=[cite(item) for item in _items(found.get("MITIGATION", []), 3)],
        guidance=[cite(item) for item in _items(found.get("GUIDANCE", []), 3)],
        reports=count, model=model, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    if not result.assessment and not result.bluf:
        raise OllamaError("Ollama's reply had no assessment")
    return result


def _ollama_json(endpoint: str, path: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    import urllib.request

    request = urllib.request.Request(endpoint.rstrip("/") + path, json.dumps(payload).encode() if payload else None,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # noqa: S310 - the operator's Ollama
        return json.loads(response.read() or b"{}")


def available_memory() -> int | None:
    """Bytes of memory the system can still give out (Linux), or None when unknown."""
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


HEADROOM = 1.3              # a model needs about this times its file size while it works
SPARE = 2 * 1024 ** 3       # left for everything else running


def make_room(endpoint: str, model: str) -> None:
    """Unload every other model from Ollama, then check the assessment model fits in memory:
    a 7B model loaded next to the classification model and a browser once ran this computer out of
    memory, and the system killed the browser. Raises OllamaError when it won't fit."""
    try:
        for loaded in _ollama_json(endpoint, "/api/ps").get("models", []):
            if loaded.get("name") != model and loaded.get("model") != model:
                _ollama_json(endpoint, "/api/generate", {"model": loaded["name"], "keep_alive": 0})
        size = next((m.get("size", 0) for m in _ollama_json(endpoint, "/api/tags").get("models", [])
                     if model in (m.get("name"), m.get("model")) or m.get("name") == f"{model}:latest"), None)
    except (OSError, ValueError) as error:
        raise OllamaError(f"Unable to contact Ollama: {error}") from error
    if size is None:
        raise OllamaError(f"model {model} isn't installed (ollama pull {model})")
    free = available_memory()
    needed = int(size * HEADROOM) + SPARE
    if free is not None and free < needed:
        raise OllamaError(f"not enough free memory for {model}: it needs about {needed / 1024 ** 3:.0f} GB and "
                          f"{free / 1024 ** 3:.0f} GB is free. Close some programs (browser tabs) and try again.")


def assess_event(brief: str, count: int, endpoint: str, model: str, timeout: float = 600.0) -> EventAssessment:
    """Ask the local model to assess an event (``brief`` from event_brief, ``count`` reports). The
    model is unloaded as soon as it answers, so it doesn't hold memory between assessments."""
    make_room(endpoint, model)
    reply = ollama_generate(endpoint, model, brief, timeout, options=OPTIONS, keep_alive=0)
    return parse_assessment(reply, count, model)


TREND_MARKS = {"escalating": "▲", "stable": "▬", "winding down": "▼", "unclear": "?"}


def without_citations(text: str) -> str:
    """Text without report numbers (for a briefing, where they mean nothing)."""
    return re.sub(r"\s+([.,;])", r"\1", _CITATION.sub("", text)).strip()


def changes(accepted: EventAssessment, proposed: EventAssessment) -> list[str]:
    """What a proposed update says that the accepted assessment doesn't."""
    found = []
    if proposed.trend != accepted.trend:
        found.append(f"Trend: {accepted.trend} → {proposed.trend}")
    levels = {without_citations(s).lower(): level for s, level in accepted.probabilities}
    for statement, level in proposed.probabilities:
        before = levels.get(without_citations(statement).lower())
        if before is None:
            found.append(f"New probability: {without_citations(statement)} | {level}")
        elif before != level:
            found.append(f"Probability: {without_citations(statement)} {before} → {level}")
    known = {without_citations(gap).lower() for gap in accepted.gaps}
    found += [f"New gap: {without_citations(gap)}" for gap in proposed.gaps if without_citations(gap).lower() not in known]
    if proposed.reports > accepted.reports:
        found.append(f"Reads {proposed.reports - accepted.reports} newer report"
                     f"{'s' if proposed.reports - accepted.reports != 1 else ''}")
    return found


# -- into a MAGNET S2 draft -------------------------------------------------------------

def source_numbers(reports: list[dict]) -> dict[int, int]:
    """Report number (as the model saw them) -> its line in the draft's SOURCE LIST."""
    from s2_watch.s2_report import _source_name

    order: dict[str, int] = {}
    for report in reports:
        order.setdefault(_source_name(report), len(order) + 1)
    return {number: order[_source_name(report)] for number, report in enumerate(reports, 1)}


def to_sources(text: str, mapping: dict[int, int]) -> str:
    """Citations of reports as citations of SOURCE LIST lines."""
    def swap(match):
        numbers = sorted({mapping[n] for n in (int(x) for x in re.findall(r"\d+", match.group(1))) if n in mapping})
        return f"[{', '.join(map(str, numbers))}]" if numbers else ""
    return re.sub(r"\s+([.,;])", r"\1", _CITATION.sub(swap, text)).strip()


def fill_report(text: str, assessment: EventAssessment, reports: list[dict]) -> str:
    """A MAGNET S2 draft with the assessment in COMMENTS / ASSESSMENT (with its probability
    table), INTELLIGENCE GAPS, MITIGATION RECOMMENDATIONS and MAGNET GUIDANCE; citations point
    at the draft's SOURCE LIST."""
    from s2_watch.s2_report import replace_section

    mapping = source_numbers(reports)
    cite = lambda value: to_sources(value, mapping)     # noqa: E731
    lead = f"Trend: {assessment.trend}." + (f" {cite(assessment.trend_reason)}" if assessment.trend_reason else "") \
        + (f" {cite(assessment.agreement)}" if assessment.agreement else "")
    paragraphs = [cite(p) for p in assessment.assessment] + [lead]
    if not assessment.accepted_by:
        paragraphs.append("(Ollama suggestion, not yet reviewed by the analyst.)")
    if assessment.reports < len(reports):
        paragraphs.append(f"(Assessed from {assessment.reports} of {len(reports)} reports; the newer ones are not yet "
                          "reflected.)")
    table = [f"{cite(statement).replace('|', '/')} | {level}" for statement, level in assessment.probabilities] or \
        ["(Assessment category, e.g. activity continues or escalates) | MODERATE"]
    body = "\n".join(paragraphs + ["", "Current Assessment:", *table,
                                   "Note: Probability levels are based on currently available reporting only and "
                                   "may be revised."])
    text = replace_section(text, "COMMENTS / ASSESSMENT", body)
    if assessment.gaps:
        text = replace_section(text, "INTELLIGENCE GAPS", "\n".join(f"- {cite(gap)}" for gap in assessment.gaps))
    if assessment.mitigation:
        text = replace_section(text, "MITIGATION RECOMMENDATIONS",
                               "\n".join(f"- {cite(item)}" for item in assessment.mitigation))
    if assessment.guidance:
        text = replace_section(text, "MAGNET GUIDANCE", "\n".join(f"- {cite(item)}" for item in assessment.guidance))
    return text
