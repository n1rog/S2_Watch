"""The FLMSG version of a report: a condensed radio summary, not the whole report in capitals.

At most FLMSG_LIMIT characters (1,500), plain upper-case ASCII wrapped for FLMSG: the title,
DTG and precedence, the bottom line, then the situation, assessment, gaps, guidance and
mitigation items in that order, as many as fit, each shortened to its first sentences, and
the operator's call sign. Background and the source list stay in the full report, and so do
citation marks ([1], [W2]); a line says when the full report holds more.

Works for S2 Watch's own MAGNET reports and briefings, its follow-up forms, and reports
written elsewhere and pasted in (pasted_report).
"""

from __future__ import annotations

import re
import textwrap

from s2_watch.followup import FLMSG_WIDTH, ascii_upper, flmsg_text
from s2_watch.s2_report import parse_report

FLMSG_LIMIT = 1500
ITEM_LIMIT = 220            # one situation line, one gap...: its first sentences up to this long
BLUF_LIMIT = 260
# The sections that go on the air, in order of what matters most, by the names reports use.
# How many items of each go on the air, so every section gets a share of the 1,500 characters.
QUOTA = {"SITUATION": 3, "ASSESSMENT": 2, "GAPS": 2, "GUIDANCE": 2, "MITIGATION": 1}
ORDER = (("SITUATION", r"situation|key\s+facts|timeline"),
         ("ASSESSMENT", r"comments|assessment|analysis|probabilit"),
         ("GAPS", r"gaps"),
         ("GUIDANCE", r"guidance"),
         ("MITIGATION", r"mitigation|recommendations"))
_CITATION = re.compile(r"\s*\[(?:W?\d{1,3}|F\d)\](?:\s*(?:[–\-,]|and)\s*\[(?:W?\d{1,3}|F\d)\])*")
_SENTENCE_END = re.compile(r"(?<=[a-z0-9)%][.!?])\s+(?=[A-Z0-9\"'(])")


def _section(name: str) -> str:
    return next((label for label, pattern in ORDER if re.search(pattern, name, re.IGNORECASE)), "")


def _short(text: str, limit: int) -> str:
    """Without citation marks; the first sentences that fit ``limit``, else cut at a word."""
    text = " ".join(_CITATION.sub(" ", text).split()).strip(" ;,")
    if len(text) <= limit:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit - 3].rsplit(" ", 1)[0] + "..."


def _wrapped(lines: list[str]) -> str:
    out = []
    for line in lines:
        out.extend(textwrap.wrap(ascii_upper(line), FLMSG_WIDTH) or [""])
    return "\n".join(out).strip() + "\n"


def condensed(title: str, dtg: str, precedence: str, bluf: str, sections: dict[str, list[str]],
              callsign: str = "", limit: int = FLMSG_LIMIT) -> str:
    """The radio summary from a report's parts; ``sections``: label (ORDER) -> items."""
    head = [f"MAGNET S2 - {title}".strip(" -")]
    stamp = " ".join(part for part in (f"DTG {dtg}" if dtg else "", f"PREC {precedence}" if precedence else "") if part)
    if stamp:
        head.append(stamp)
    if bluf:
        head.append(f"BLUF: {_short(bluf, BLUF_LIMIT)}")
    tail = [f"DE {callsign.upper()}"] if callsign else []
    more = "MORE IN FULL REPORT"
    body: list[str] = []
    left_out = full = False
    for label, _pattern in ORDER:
        items = []
        for item in (_short(item, ITEM_LIMIT) for item in sections.get(label, []) if item.strip()):
            words = set(re.findall(r"[a-z0-9]+", item.lower()))
            if item and not any(words <= set(re.findall(r"[a-z0-9]+", kept.lower())) for kept in items):
                items.append(item)       # the same report told twice (situation and timeline) goes once
        if len(items) > QUOTA[label]:
            items, left_out = items[:QUOTA[label]], True
        if not items:
            continue
        added_heading = False
        for item in items:
            trial = body + ([f"{label}:"] if not added_heading else []) + [f"- {item}"]
            if len(_wrapped(head + trial + [more] + tail)) > limit:
                left_out = full = True
                break
            body = trial
            added_heading = True
        if full:
            break
    text = _wrapped(head + body + ([more] if left_out else []) + tail)
    while len(text) > limit and len(head) > 1:     # an unusually long title or BLUF
        head[-1] = head[-1][:max(40, len(head[-1]) - (len(text) - limit) - 4)].rsplit(" ", 1)[0] + "..."
        text = _wrapped(head + body + ([more] if left_out else []) + tail)
    return text[:limit]


def _native_parts(text: str):
    report = parse_report(text)
    sections: dict[str, list[str]] = {}
    bluf = ""
    for name, blocks in report.sections:
        items: list[str] = []
        for block in blocks:
            if block.kind == "paragraph" and not block.text.startswith("("):
                items.append(block.text)
            elif block.kind == "bullets":
                items += [str(row) for row in block.rows if not str(row).startswith("(")]
            elif block.kind == "timeline":
                items += [f"{row[0]}: {row[1]}" for row in block.rows if len(row) >= 2]
            elif block.kind == "probability":
                items += [f"{row[0]} - {row[1]}" for row in block.rows
                          if len(row) >= 2 and not str(row[0]).startswith("(")]
        if name.upper().startswith("SUMMARY"):
            bluf = " ".join(items)
        elif _section(name):
            sections.setdefault(_section(name), []).extend(items)
    return report.subject, report.dtg, report.fields.get("PRECEDENCE", ""), bluf, sections


def _pasted_parts(text: str):
    from s2_watch import pasted_report as pasted

    sections: dict[str, list[str]] = {}
    current = ""
    for block in pasted.blocks(text):
        if block.kind == "heading":
            current = _section(block.text)
            continue
        if not current or block.kind == "rule":
            continue
        if block.kind == "table":
            rows = block.rows[1:] if len(block.rows) > 1 else block.rows
            sections.setdefault(current, []).extend(" - ".join(cell for cell in row if cell) for row in rows)
        else:
            sections.setdefault(current, []).append(pasted._plain_inline(block.text))
    return (pasted.title(text), pasted.stated_dtg(text), pasted.stated_precedence(text), pasted.bluf_text(text),
            sections)


def flmsg_for(text: str, callsign: str = "") -> str:
    """The FLMSG version of any report: at most FLMSG_LIMIT characters."""
    from s2_watch.pasted_report import is_pasted

    if is_pasted(text):
        return condensed(*_pasted_parts(text), callsign=callsign)
    report = parse_report(text)
    if report.fields.get("SUBJECT") and report.sections:
        return condensed(*_native_parts(text), callsign=callsign)
    # A follow-up form: labelled lines, already short; kept whole up to the limit.
    out = flmsg_text(text)
    if len(out) <= FLMSG_LIMIT:
        return out
    lines, kept = out.splitlines(), []
    for line in lines:
        if len("\n".join(kept + [line, "MORE IN FULL REPORT"])) + 1 > FLMSG_LIMIT:
            break
        kept.append(line)
    return "\n".join(kept + ["MORE IN FULL REPORT"]) + "\n"
