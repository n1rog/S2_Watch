"""MAGNET S2 Intelligence Reports: drafted from S2 Watch's reports and events.

The draft is plain text the operator edits, in the report's own structure: the routing
fields (SUBJECT: ... MAGCON STATUS:), then the sections in order (SUMMARY (BLUF) through
SOURCE LIST), then TAGS:. The Word document, the web page, and the FLMSG and JS8Call
versions are all made from that text, so the operator's edits carry through.

Text conventions inside a section (so the export can lay it out):
- a short line ending in ":" alone is a sub-header ("Timeline of Known Activity:")
- "time | event" lines form a timeline table; "category | HIGH" lines a probability table
- "- item" lines are bullets; "[1] Name [A1] — description — https://..." lines are sources
- "Note: ..." is a note; "(...)" alone is a prompt for the operator, left in until replaced

S2 Watch fills what it knows (routing, BLUF, Situation, timeline, gaps, sources); the
analysis sections start as prompts, since judgment is the analyst's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from s2_watch.followup import JS8CALL_TARGET, JS8CALL_UNSAFE, ascii_upper

SECTIONS = ("SUMMARY (BLUF)", "BACKGROUND", "SITUATION", "COMMENTS / ASSESSMENT", "INTELLIGENCE GAPS",
            "MITIGATION RECOMMENDATIONS", "MAGNET GUIDANCE", "SOURCE LIST")
HEADER_FIELDS = ("SUBJECT", "PURPOSE", "DTG", "REPORTING PERIOD", "GEOGRAPHIC FOCUS", "PRECEDENCE",
                 "MAGCON STATUS", "SOURCES")
HEADER_LABELS = {"SUBJECT": "Subject", "PURPOSE": "Purpose", "DTG": "DTG", "REPORTING PERIOD": "Reporting Period",
                 "GEOGRAPHIC FOCUS": "Geographic Focus", "PRECEDENCE": "Precedence",
                 "MAGCON STATUS": "MagCon Status", "SOURCES": "Sources"}
DEFAULT_SOURCES = "Multiple source reporting from Local, National, and International platforms. See source list."
DEFAULT_MAGCON = "3 – ELEVATED (NO CHANGE)"
CLOSING = ("Submit reports through established MAGNET situational awareness channels.  "
           "To Learn More About MAGNET, Visit www.MAGNETHF.COM")
TOP_BANNER = "UNCLASSIFIED  //  OSINT  –  MAGNET S2 OSINT TEAM"     # "FOR OFFICIAL USE" removed by instruction
PRECEDENCE = {"high": "IM – IMMEDIATE", "medium": "PR – PRIORITY", "low": "RR – ROUTINE"}
LEVELS = ("HIGH", "MODERATE", "LOW")
MAX_TAGS = 15
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
OFFICIAL_SOURCES = {"USGS", "NIFC", "NOAA SPC", "FEMA", "FAA", "U.S. Department of State", "NWS"}


def magnet_dtg(when: datetime) -> str:
    """MAGNET date-time group: YYMMDD-HHMMZ (Zulu)."""
    when = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return when.strftime("%y%m%d-%H%MZ")


def _utc(value) -> datetime:
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def day_label(when: datetime) -> str:
    return f"{when.day:02d} {MONTHS[when.month - 1]}"


def period_label(start: datetime, end: datetime) -> str:
    """"29–30 May 2026", "30 May 2026", "30 Sep – 01 Oct 2026"."""
    start, end = _utc(start), _utc(end)
    if start.date() == end.date():
        return f"{start.day:02d} {MONTHS[start.month - 1]} {start.year}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day:02d}–{end.day:02d} {MONTHS[end.month - 1]} {end.year}"
    return f"{day_label(start)} – {day_label(end)} {end.year}"


def timeline_time(value) -> str:
    when = _utc(value)
    return f"{when:%H%M}Z {day_label(when)}"


def _official(report: dict) -> bool:
    """An official source: a government feed, or one the operator entered as official."""
    return report["source"] == "OSINT" and (report.get("sender") in OFFICIAL_SOURCES or bool(report.get("official")))


def admiralty(source: str, sender: str, confirmed: bool, independent_sources: int, grade: str = "",
              official: bool = False) -> str:
    """A suggested Admiralty rating (reliability A–F, credibility 1–6) for the analyst to adjust.

    Official government sources are A; news organisations C; a radio station gets the grade
    its track record suggests (s2_watch.senders), F when it can't be judged yet. Credibility
    is 1 when another independent source confirms the report, 2 when several sources agree,
    6 when it cannot yet be judged.
    """
    if source == "OSINT" and (sender in OFFICIAL_SOURCES or official):
        reliability = "A"
    elif source == "OSINT":
        reliability = "C"
    else:
        reliability = grade or "F"
    credibility = "1" if confirmed else "2" if independent_sources > 1 else "6"
    return f"[{reliability}{credibility}]"


def _url(fields: dict) -> str:
    return (fields or {}).get("url", "")


def _source_name(report: dict) -> str:
    sender = report.get("sender") or ""
    if not sender and report["source"] == "JS8Call":   # JS8Call's record carries the callsign
        match = re.search(r'"FROM":\s*"([A-Z0-9/]+)"', report.get("text") or "")
        sender = match.group(1) if match else ""
    if report["source"] == "OSINT":
        return sender or "Open source"
    return f"{report['source']} ({sender})" if sender else report["source"]   # "—" separates source-line parts


def source_lines(reports: list[dict], confirmed: bool, grades: dict[str, str] | None = None) -> list[str]:
    """Numbered source list, one line per distinct source, in first-heard order. ``grades``:
    call sign -> the reliability its track record suggests."""
    from s2_watch.senders import callsign

    grades = grades or {}
    independent = len({r["source"] for r in reports})
    lines, seen = [], set()
    for report in reports:
        name = _source_name(report)
        if name in seen:
            continue
        seen.add(name)
        station = callsign(report["source"], report.get("sender") or "", report.get("text") or "")
        rating = admiralty(report["source"], report.get("sender") or "", confirmed, independent, grades.get(station, ""),
                           bool(report.get("official")))
        heard = f"heard {timeline_time(report['first_seen'])}"
        handling = (report.get("fields") or {}).get("handling", "")
        what = f"analyst entry, {handling}" if (report.get("fields") or {}).get("type") == "ANALYST ENTRY" else \
            "official report" if _official(report) else \
            "open-source report" if report["source"] == "OSINT" else "radio report"
        url = _url(report.get("fields") or {})
        lines.append(f"[{len(lines) + 1}] {name} {rating} — {what}, {heard}" + (f" — {url}" if url else ""))
    return lines


def make_tags(*texts: str, extra: tuple[str, ...] = ()) -> list[str]:
    """Up to 15 WordPress tags: categories and places first, then distinctive words."""
    stop = {"the", "and", "for", "with", "from", "that", "this", "report", "reports", "reported", "radio", "magnet",
            "official", "source", "sources", "heard", "region", "mars", "assessment", "update"}
    tags: list[str] = []
    for tag in extra:
        tag = tag.strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    for text in texts:
        for word in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text):
            word = word.lower().strip("'-")
            if word not in stop and word not in tags:
                tags.append(word)
            if len(tags) >= MAX_TAGS:
                return tags
    return tags[:MAX_TAGS]


def topic_for(subject: str) -> str:
    """The 2–4 word file-name topic: uppercase, underscores."""
    words = [word for word in re.findall(r"[A-Za-z0-9]+", subject.upper())
             if word not in {"THE", "AND", "OF", "IN", "AT", "ON", "A", "AN", "FOR", "TO"}]
    return "_".join(words[:4]) or "REPORT"


def file_stem(subject: str, dtg: str) -> str:
    """MAGNET_S2_TOPIC_YYMMDD-HHMMZ (the extension is added by the export)."""
    return f"MAGNET_S2_{topic_for(subject)}_{dtg.strip().upper()}"


def _header(fields: dict) -> list[str]:
    return [f"{key}: {fields.get(key, '')}" for key in HEADER_FIELDS]


def _place(state: str, region, label: str = "") -> str:
    from s2_watch.locating import load_gazetteer

    state_name = load_gazetteer().state_names.get(state or "", "")
    if label.startswith("Grid ") or label.startswith("StatRep area"):   # a grid square: name the state too
        grid = label.split("grid", 1)[-1].split("Grid", 1)[-1].strip()
        label = f"{state_name} (grid {grid})" if state_name else f"Grid {grid}"
    parts = [part for part in (label or state_name, f"MARS Region {region}" if region else "") if part]
    return ", ".join(dict.fromkeys(parts)) or "United States"


# -- follow-up report (one report or event) ----------------------------------------------

def followup_report(facts, members: list[dict] | None = None, now: datetime | None = None,
                    magcon: str = DEFAULT_MAGCON, grades: dict[str, str] | None = None,
                    background: list[str] | None = None) -> str:
    """A MAGNET S2 report about one report and the event it belongs to. ``background``: the
    BACKGROUND lines S2 Watch could state (s2_watch.background); else just the prompt."""
    now = now or datetime.now(timezone.utc)
    members = members or [{"first_seen": facts.message.received_at.isoformat(), "source": facts.message.source_application,
                           "sender": facts.message.fields.get("sender", ""), "summary": facts.result.summary,
                           "text": facts.message.raw_content, "corroboration": facts.official, "urgency": facts.result.urgency,
                           "fields": facts.message.fields}]
    first = _utc(members[0]["first_seen"])
    last = _utc(max(member["first_seen"] for member in members))
    point = next((location for location in facts.locations if getattr(location, "kind", "news") != "region"), None)
    label = "" if point is None or point.label.startswith("Coordinates") else point.label
    focus = _place(facts.state, facts.region, label)
    official = facts.official.lstrip("✓ ").strip()
    official_members = [m for m in members if _official(m)]
    confirmed = bool(official or official_members)
    radio = [m for m in members if m["source"] != "OSINT"]
    sources = sorted({m["source"] for m in members})
    subject = facts.result.summary.replace("Coordinates in report", focus.split(",")[0])
    exercise = any(m.get("exercise") == "exercise" for m in members) or subject.upper().startswith("EXERCISE")
    if exercise and not subject.upper().startswith("EXERCISE"):
        subject = f"EXERCISE: {subject}"
    fields = {
        "SUBJECT": subject,
        "PURPOSE": f"Provide intelligence assessment of {facts.result.category.lower()} reporting in {focus} "
                   f"and evaluate its significance for MAGNET members.",
        "DTG": magnet_dtg(now),
        "REPORTING PERIOD": period_label(first, last),
        "GEOGRAPHIC FOCUS": focus,
        "PRECEDENCE": PRECEDENCE.get(facts.result.urgency, PRECEDENCE["low"]),
        "MAGCON STATUS": magcon,
        "SOURCES": DEFAULT_SOURCES,
    }
    count = len(members)
    when = (f"on {period_label(first, last)}" if first.date() == last.date()
            else f"between {period_label(first, last)}")
    names = " and ".join(sources) if len(sources) <= 2 else ", ".join(sources[:-1]) + f" and {sources[-1]}"
    bluf = [f"{count} report{'s' if count != 1 else ''} from {names} {when} concern {subject.split(' — ')[0]} "
            f"in {focus}."]
    if confirmed:
        names = official or ", ".join(sorted({m['sender'] for m in official_members}))
        bluf.append(f"Official reporting ({names}) corroborates the activity.")
    else:
        bluf.append("No official source has corroborated the reporting as of this DTG.")
    if facts.result.urgency == "high":
        bluf.append("Reporting indicates an immediate threat to life or critical infrastructure.")
    situation = []
    for member in members:
        words = " ".join((member.get("text") or "").split())
        if member["source"] == "JS8Call" and words.startswith("{"):
            words = member.get("summary") or words
        situation.append(f"{timeline_time(member['first_seen'])}: {_source_name(member)} reported: "
                         f"\"{words[:400]}\"")
    # The time the message gives (a relay may be heard hours later), else when it was heard.
    timeline = [f"~{timeline_time(m.get('event_time') or m['first_seen'])} | {_source_name(m)}: "
                f"{m.get('summary') or ' '.join(m['text'].split())[:160]}" for m in members]
    gaps = []
    if not confirmed:
        gaps.append("Has any official source (NWS, emergency management, law enforcement) confirmed the reporting?")
    if point is None:
        gaps.append("What is the exact location? Reporting gives only a general area.")
    if len(radio) <= 1:
        gaps.append("Are other stations in the area reporting the same activity?")
    gaps.append("What is the current status: ongoing, contained, or resolved?")
    lines = _header(fields) + [
        "", "SUMMARY (BLUF)", " ".join(bluf),
        "", "BACKGROUND", *(background or ["(Factual context only, no analysis: what is normal for this area or hazard, "
                                           "and any earlier related incidents.)"]),
        "", "SITUATION", *situation, "", "Timeline of Known Activity:", *timeline,
        "", "COMMENTS / ASSESSMENT",
        "(Analytical layer in measured language: assessed, likely, indicates, suggests. Most significant indicator first.)",
        "", "Current Assessment:", "(Assessment category, e.g. activity continues or escalates) | MODERATE",
        "Note: Probability levels are based on currently available reporting only and may be revised.",
        "", "INTELLIGENCE GAPS", *[f"- {gap}" for gap in gaps],
        "", "MITIGATION RECOMMENDATIONS",
        "- Continue monitoring radio and open-source reporting for updates and official confirmation.",
        "- (Add actionable recommendations tied to the specific threats identified.)",
        "", "MAGNET GUIDANCE", "(Operator-specific actions; reference the PACE tier if communications posture is relevant.)",
        "", "SOURCE LIST", *source_lines(members, confirmed, grades),
        "", "TAGS: " + ", ".join(make_tags(subject, focus, extra=(facts.result.category, "MAGNET S2", *sources))),
    ]
    return _exercise_banner(lines) if exercise else "\n".join(lines)


def _exercise_banner(lines: list[str]) -> str:
    """Exercise traffic is marked at the top and bottom, as exercise messages are."""
    from s2_watch.exercise import BANNER

    return "\n".join([BANNER, "", *lines, "", BANNER])


# -- shift briefing (a period) ---------------------------------------------------------

def briefing_report(events, now: datetime, hours: int, focus: set[int] | None = None,
                    last_briefing: datetime | None = None, magcon: str = DEFAULT_MAGCON,
                    reports_of=None, grades: dict[str, str] | None = None, quiet_for=None,
                    background: list[str] | None = None, assessments: dict | None = None) -> str:
    """A MAGNET S2 report summarising a watch period's events. ``assessments``: event id -> the
    analyst's accepted assessment, listed under COMMENTS / ASSESSMENT. Closed events are listed as
    resolved, not as current activity; quiet ones say how long they've been quiet."""
    from s2_watch.events import quiet_hours
    from s2_watch.briefing import PRIORITY_ITEMS, URGENCY_RANK, priority_key

    from s2_watch.analysis.categories import NEWS_CATEGORIES
    from s2_watch.analysis.ollama import readable_text
    from s2_watch.models import Message

    focus = set(focus or ())
    events = [e for e in events if not (e.statuses and set(e.statuses) == {"ignored"})]
    titles = {}
    for event in events:   # a title that is only a category says nothing: use the report's own words
        title = event.title.strip()
        if (title in NEWS_CATEGORIES or len(title) < 8) and reports_of is not None:
            first_report = (reports_of(event.event_id) or [None])[0]
            if first_report:
                words = " ".join(readable_text(Message(first_report["source"], first_report["text"])).split())
                title = words[:90] + ("..." if len(words) > 90 else "")
        if getattr(event, "exercise", False) and not title.upper().startswith("EXERCISE"):
            title = f"EXERCISE: {title}"
        titles[event.event_id] = title
    exercise = any(getattr(event, "exercise", False) for event in events)
    resolved = [e for e in events if getattr(e, "closed_at", None) is not None]
    events = [e for e in events if getattr(e, "closed_at", None) is None]
    ranked = sorted(events, key=lambda e: priority_key(e, focus), reverse=True)
    priority = [e for e in ranked if e.urgency in ("high", "medium")
                and (e.region or any(source != "OSINT" for source in e.sources))][:PRIORITY_ITEMS]
    start = now - timedelta(hours=hours)
    from s2_watch.focus import describe as describe_focus

    from s2_watch import world

    chosen = describe_focus(focus)
    area = chosen or ("the United States" if world.COVERAGE == {"us"} else world.describe())
    confirmed = sum(bool(e.official) for e in events)
    new = [e for e in events if last_briefing and e.first_seen > last_briefing]
    top = priority[0] if priority else None
    fields = {
        "SUBJECT": ("EXERCISE: " if exercise else "") + f"Situation Briefing – {area}",
        "PURPOSE": f"Provide a situational summary of newsworthy reporting for {area} over the last {hours} hours.",
        "DTG": magnet_dtg(now),
        "REPORTING PERIOD": period_label(start, now),
        "GEOGRAPHIC FOCUS": chosen or ("United States" if world.COVERAGE == {"us"} else world.describe()),
        "PRECEDENCE": PRECEDENCE["high"] if top and top.urgency == "high" and top.reports > 1 else PRECEDENCE["low"],
        "MAGCON STATUS": magcon,
        "SOURCES": DEFAULT_SOURCES,
    }
    counts = {level: sum(e.urgency == level for e in events) for level in ("high", "medium")}
    reported = "remain open" if resolved else "were reported in the period"
    bluf = [f"{len(events)} events {reported} ({counts['high']} high and {counts['medium']} medium urgency); "
            f"{confirmed} are corroborated by official sources."]
    if top:
        bluf.insert(0, f"Most significant: {titles[top.event_id]} ({top.state or 'location unknown'}).")
    if last_briefing:
        bluf.append(f"{len(new)} are new since the last briefing.")
    if resolved:
        bluf.append(f"{len(resolved)} event{'s' if len(resolved) != 1 else ''} closed in the period.")
    situation = []
    timeline = []
    for event in priority:
        status = f"Corroborated by {event.official}" if event.official else "Not yet corroborated by official sources"
        lead = f"; radio reported {event.radio_lead_minutes} min ahead" if event.radio_lead_minutes else ""
        flag = " [NEW]" if event in new else ""
        quiet = event.status(now) == "quiet" if hasattr(event, "status") else False
        quiet_note = f"; no new reports for {quiet_hours(now - event.last_seen)} hours" if quiet else ""
        silent = quiet_for(event) if quiet_for else []
        if silent:
            quiet_note += (f"; radio silence near {silent[0]['grid']} (no traffic for {silent[0]['silent_hours']} hours, "
                           f"usually about {silent[0]['usual_per_3h']} messages every 3 hours)")
        if hasattr(event, "single_source") and event.single_source(now):
            status = "Single source, not confirmed by any other station or official source"
        situation += [f"{titles[event.event_id]}:",
                      f"{event.reports} report{'s' if event.reports != 1 else ''} from {', '.join(event.sources)}; "
                      f"{status}{lead}; urgency {event.urgency.upper()}{quiet_note}.{flag}", ""]
        timeline.append(f"~{timeline_time(event.first_seen)} | {titles[event.event_id]}")
    by_category: dict[str, int] = {}
    for event in events:
        by_category[event.category] = by_category.get(event.category, 0) + 1
    other = "; ".join(f"{category} {count}" for category, count in sorted(by_category.items(), key=lambda kv: -kv[1]))
    gaps = [f"- Can another station or an official source confirm \"{titles[e.event_id]}\"? (single source)"
            if hasattr(e, "single_source") and e.single_source(now)
            else f"- Is \"{titles[e.event_id]}\" confirmed by an official source?" for e in priority if not e.official][:6]
    gaps = gaps or ["- (No open questions identified.)"]
    # The analyst's accepted assessments of the period's events (their report numbers mean nothing here).
    from s2_watch.event_assessment import without_citations

    assessed, probability = [], []
    for event in priority:
        accepted = (assessments or {}).get(event.event_id)
        if accepted is None:
            continue
        when = timeline_time(accepted.accepted_at) if accepted.accepted_at else ""
        summary = without_citations(accepted.bluf or " ".join(accepted.assessment[:1]))
        assessed.append(f"{titles[event.event_id]}: assessed {accepted.trend.upper()} ({when}, {accepted.credit}). "
                        f"{summary}")
        probability += [f"{titles[event.event_id]}: {without_citations(statement)} | {level}"
                        for statement, level in accepted.probabilities[:2]]
    sources: list[dict] = []
    if reports_of is not None:
        for event in priority:
            sources.extend(reports_of(event.event_id))
    source_list = source_lines(sorted(sources, key=lambda r: r["first_seen"]), confirmed > 0, grades) if sources else \
        ["[1] S2 Watch event log — radio and open-source reporting for the period"]
    lines = _header(fields) + [
        "", "SUMMARY (BLUF)", " ".join(bluf),
        "", "BACKGROUND", *(background or ["(Factual context for the period: standing conditions, MagCon basis, "
                                           "ongoing situations.)"]),
        "", "SITUATION", *(situation or ["No high- or medium-urgency events in the period.", ""]),
        "Other Activity:", f"Events by category: {other or 'none'}.",
        *(["", "Resolved in the Period:"] if resolved else []),
        *[f"- {titles[e.event_id]}: {e.close_reason or 'closed'} ({timeline_time(e.closed_at)})" for e in resolved],
        "", "Timeline of Known Activity:", *(timeline or ["— | No priority events"]),
        "", "COMMENTS / ASSESSMENT",
        "(Analytical layer in measured language: assessed, likely, indicates, suggests. Overall situation first.)",
        *assessed,
        "", "Current Assessment:", *(probability or ["(Assessment category) | MODERATE"]),
        "Note: Probability levels are based on currently available reporting only and may be revised.",
        "", "INTELLIGENCE GAPS", *gaps,
        "", "MITIGATION RECOMMENDATIONS",
        "- Continue monitoring the listed events for official confirmation and change.",
        "- (Add actionable recommendations tied to the specific threats identified.)",
        "", "MAGNET GUIDANCE", "(Operator-specific actions; reference the PACE tier if communications posture is relevant.)",
        "", "SOURCE LIST", *source_list,
        "", "TAGS: " + ", ".join(make_tags(*(titles[e.event_id] for e in priority), extra=("MAGNET S2", "situation briefing",
                                                                            *sorted(by_category)))),
    ]
    return _exercise_banner(lines) if exercise else "\n".join(lines)


# -- reading an (edited) report back -----------------------------------------------------

@dataclass
class Block:
    kind: str          # paragraph, subheader, bullets, timeline, probability, sources, note, prompt
    text: str = ""
    rows: list = field(default_factory=list)


@dataclass
class S2Report:
    fields: dict
    sections: list      # [(name, [Block])]
    tags: list[str]

    @property
    def dtg(self) -> str:
        return self.fields.get("DTG", "")

    @property
    def subject(self) -> str:
        return self.fields.get("SUBJECT", "")


SOURCE_LINE = re.compile(r"^\[(\d+)\]\s*(.+?)\s*(\[[A-F][1-6]\])?\s*—\s*(.*)$")
URL = re.compile(r"https?://\S+")


def _section_name(line: str) -> str | None:
    name = line.strip().rstrip(":").upper()
    return next((section for section in SECTIONS if section == name), None)


def parse_report(text: str) -> S2Report:
    fields: dict[str, str] = {}
    sections: list[tuple[str, list[Block]]] = []
    tags: list[str] = []
    current: list[Block] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("TAGS:"):
            tags = [tag.strip() for tag in line.split(":", 1)[1].split(",") if tag.strip()][:MAX_TAGS]
            continue
        name = _section_name(line)
        if name:
            current = []
            sections.append((name, current))
            continue
        if current is None:
            key, _, value = line.partition(":")
            if key.strip().upper() in HEADER_FIELDS:
                fields[key.strip().upper()] = value.strip()
            continue
        add_block(current, line)
    return S2Report(fields, sections, tags)


def add_block(blocks: list[Block], line: str) -> None:
    last = blocks[-1] if blocks else None
    source = SOURCE_LINE.match(line)
    if source:
        number, name, rating, rest = source.groups()
        url = URL.search(rest)
        description = URL.sub("", rest).strip(" —")
        row = (number, name, rating or "", description, url.group(0) if url else "")
        if last and last.kind == "sources":
            last.rows.append(row)
        else:
            blocks.append(Block("sources", rows=[row]))
        return
    if " | " in line:
        left, right = [part.strip() for part in line.split(" | ", 1)]
        kind = "probability" if right.upper() in LEVELS else "timeline"
        if last and last.kind == kind:
            last.rows.append((left, right))
        else:
            blocks.append(Block(kind, rows=[(left, right)]))
        return
    if line.startswith("- "):
        if last and last.kind == "bullets":
            last.rows.append(line[2:].strip())
        else:
            blocks.append(Block("bullets", rows=[line[2:].strip()]))
        return
    if line.lower().startswith("note:"):
        blocks.append(Block("note", line))
    elif line.startswith("(") and line.endswith(")"):
        blocks.append(Block("prompt", line))
    elif line.endswith(":") and len(line) <= 80 and not line.startswith("~"):
        blocks.append(Block("subheader", line[:-1]))
    else:
        blocks.append(Block("paragraph", line))


def replace_section(text: str, name: str, body: str) -> str:
    """The report text with one section's body replaced (the heading kept)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _section_name(line) == name), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines))
                if _section_name(lines[i]) or lines[i].strip().upper().startswith("TAGS:")), len(lines))
    return "\n".join(lines[:start + 1] + body.strip().splitlines() + [""] + lines[end:])


def fill_assessment(text: str, paragraphs: list[str], gaps: list[str]) -> str:
    """Put suggested assessment paragraphs in place of COMMENTS / ASSESSMENT's prompt (keeping
    its probability table) and add suggested gaps not already listed."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _section_name(line) == "COMMENTS / ASSESSMENT"), None)
    if start is not None and paragraphs:
        end = next((i for i in range(start + 1, len(lines)) if _section_name(lines[i])), len(lines))
        prompt = next((i for i in range(start + 1, end) if lines[i].strip().startswith("(")), None)
        if prompt is not None:
            lines[prompt:prompt + 1] = paragraphs
        else:
            lines[start + 1:start + 1] = paragraphs
    text = "\n".join(lines)
    if gaps:
        report = parse_report(text)
        existing = {item.lower() for name, blocks in report.sections if name == "INTELLIGENCE GAPS"
                    for block in blocks if block.kind == "bullets" for item in block.rows}
        current = [line for name, blocks in report.sections if name == "INTELLIGENCE GAPS"
                   for block in blocks if block.kind == "bullets" for line in (f"- {item}" for item in block.rows)]
        added = [f"- {gap}" for gap in gaps if gap.lower() not in existing]
        text = replace_section(text, "INTELLIGENCE GAPS", "\n".join(current + added))
    return text


# -- radio versions --------------------------------------------------------------------

def js8_report(text: str, callsign: str = "") -> str:
    """A short JS8Call message from the report: DTG, precedence, subject, the BLUF's first sentence."""
    report = parse_report(text)
    precedence = report.fields.get("PRECEDENCE", "").split(" ")[0]
    bluf = next((block.text for name, blocks in report.sections if name == "SUMMARY (BLUF)"
                 for block in blocks if block.kind == "paragraph"), "")
    first = re.split(r"(?<=\.)\s", bluf, maxsplit=1)[0] if bluf else ""
    head = f"MAGNET S2 {report.dtg} {precedence} {report.subject}".strip()
    tail = f" DE {callsign.upper()}" if callsign else ""
    text_out = head
    if first and len(ascii_upper(f"{head}: {first}{tail}")) <= JS8CALL_TARGET:
        text_out = f"{head}: {first}"
    return JS8CALL_UNSAFE.sub("", ascii_upper(text_out + tail)).strip()
