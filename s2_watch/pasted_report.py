"""Reports written elsewhere (a Claude research conversation, or by hand) and pasted into S2 Watch.

They are usually Markdown: ``#`` headings, ``**bold**``, bullet and numbered lists, ``|``
tables, ``[text](link)``. S2 Watch's own MAGNET reports have their own layout and writers
(s2_report, s2_docx, s2_html); a pasted one keeps its own structure here: a Word file and a
web page with the same headings, lists, bold and tables, plain text for FLMSG, and a short
JS8Call line from its title and bottom line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

from s2_watch.followup import JS8CALL_TARGET, JS8CALL_UNSAFE, ascii_upper, report_fields
from s2_watch.s2_report import parse_report

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_BULLET = re.compile(r"^[-*•+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\d{1,3})[.)]\s+(.*)$")
_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")
# A sentence ends at . ! or ? after a lower-case word or a number, before a capital: not "U.S." or "D.C."
_SENTENCE_END = re.compile(r"(?<=[a-z0-9)%][.!?])\s+(?=[A-Z0-9\"'(])")
_INLINE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s)<>]+)|`([^`]+)`")


@dataclass
class Block:
    kind: str                         # heading, paragraph, bullet, numbered, table, rule
    text: str = ""
    level: int = 0                    # heading level; list number
    rows: list[list[str]] = field(default_factory=list)


def magnet_dtg(when) -> str:
    """The MAGNET date-time group, YYMMDD-HHMMZ (261108-1800Z)."""
    from datetime import timezone

    return when.astimezone(timezone.utc).strftime("%y%m%d-%H%MZ")


# Section names a report written elsewhere uses, as plain lines when copied from a chat's display
# ("Situation", "Comments / Assessment"), so they are read as headings.
SECTION_NAMES = re.compile(
    r"^(?:summary(?:\s*\(bluf\))?|bluf|bottom\s+line(?:\s+up\s+front)?|background|situation|key\s+facts|timeline|"
    r"comments(?:\s*/\s*assessment)?|assessment|analysis|probabilit(?:y|ies)|intelligence\s+gaps|gaps|"
    r"mitigation(?:\s+recommendations)?|recommendations|magnet\s+guidance|guidance|source\s+list|sources|"
    r"references)$", re.IGNORECASE)
# A citation chip copied from a chat's display: a lone outlet or site name ("komu", "kmiz.com").
_CHIP = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,24}$")
_DTG = re.compile(r"\bDTG\**\s*[:\-]?\s*\**\s*(\d{6}-\d{4}Z)", re.IGNORECASE)
_PRECEDENCE = re.compile(r"\bPrecedence\**\s*[:\-]\s*\**\s*((?:FL|IM|PR|RR)\s*[–\-]\s*[A-Za-z]+)", re.IGNORECASE)


def is_native(text: str) -> bool:
    """An S2 Watch report or briefing (MAGNET header with a SUBJECT line), or a follow-up form."""
    return bool(parse_report(text).fields.get("SUBJECT") or report_fields(text).get("SUBJECT"))


def is_pasted(text: str) -> bool:
    """A report written elsewhere (Markdown, or text copied from a chat): not one of S2 Watch's own."""
    return bool(text.strip()) and not is_native(text)


def stated_dtg(text: str) -> str:
    found = _DTG.search(text)
    return found.group(1).upper() if found else ""


def stated_precedence(text: str) -> str:
    found = _PRECEDENCE.search(text)
    return re.sub(r"\s*[–\-]\s*", " - ", found.group(1)).upper() if found else ""


def blocks(text: str) -> list[Block]:
    out: list[Block] = []
    paragraph: list[str] = []
    table: list[list[str]] = []

    def flush() -> None:
        if paragraph:
            out.append(Block("paragraph", " ".join(paragraph)))
            paragraph.clear()
        if table:
            out.append(Block("table", rows=[row[:] for row in table]))
            table.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.count("|") >= 2:
            if paragraph:
                out.append(Block("paragraph", " ".join(paragraph)))
                paragraph.clear()
            if not _TABLE_RULE.match(line):
                table.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        if table:
            flush()
        if not line:
            flush()
            continue
        if _CHIP.match(line):
            continue          # a citation chip from a chat's display, not text
        heading, bullet, numbered = _HEADING.match(line), _BULLET.match(line), _NUMBERED.match(line)
        if not heading and SECTION_NAMES.match(line.strip("*:# ")):
            flush()
            out.append(Block("heading", line.strip("*:# "), 2))
            continue
        if _RULE.match(line):
            flush()
            out.append(Block("rule"))
        elif heading:
            flush()
            out.append(Block("heading", heading.group(2), len(heading.group(1))))
        elif bullet:
            flush()
            out.append(Block("bullet", bullet.group(1)))
        elif numbered:
            flush()
            out.append(Block("numbered", numbered.group(2), int(numbered.group(1))))
        else:
            # A line ending a sentence is its own paragraph (text copied from a chat's display puts
            # each item on one line); a line that doesn't continues one (Markdown wrapped by hand).
            if paragraph and re.search(r"[.!?:)\]\"”]$", paragraph[-1]):
                out.append(Block("paragraph", " ".join(paragraph)))
                paragraph.clear()
            paragraph.append(line)
    flush()
    return out


def spans(text: str) -> list[tuple[str, bool, str]]:
    """Inline text as (words, bold, link) pieces."""
    pieces, at = [], 0
    for match in _INLINE.finditer(text):
        if match.start() > at:
            pieces.append((text[at:match.start()], False, ""))
        bold, bold2, label, url, bare, code = match.groups()
        if bold or bold2:
            pieces.append((bold or bold2, True, ""))
        elif label:
            pieces.append((label, False, url))
        elif bare:
            pieces.append((bare, False, bare))
        else:
            pieces.append((code, False, ""))
        at = match.end()
    if at < len(text):
        pieces.append((text[at:], False, ""))
    return pieces


def _plain_inline(text: str) -> str:
    return "".join(words if not link or link == words else f"{words} ({link})" for words, _bold, link in spans(text))


def plain_text(text: str) -> str:
    """The report without Markdown marks, for radio (FLMSG) and plain-text files."""
    lines: list[str] = []
    for block in blocks(text):
        if block.kind == "heading":
            lines += ["", _plain_inline(block.text).upper()]
        elif block.kind == "paragraph":
            lines += [_plain_inline(block.text), ""]
        elif block.kind == "bullet":
            lines.append(f"- {_plain_inline(block.text)}")
        elif block.kind == "numbered":
            lines.append(f"{block.level}. {_plain_inline(block.text)}")
        elif block.kind == "table":
            lines += [" | ".join(_plain_inline(cell) for cell in row) for row in block.rows] + [""]
        elif block.kind == "rule":
            lines.append("")
    text_out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text_out).strip() + "\n"


def title(text: str) -> str:
    """The report's name: its opening "#" heading or line, not a section name or the BLUF."""
    for raw in text.splitlines():
        line = _plain_inline(raw.strip()).strip(" *#")
        if not line or _CHIP.match(line):
            continue
        if SECTION_NAMES.match(line.strip(":")) or re.match(r"^(?:BLUF|bottom\s+line)\b", line, re.IGNORECASE):
            break
        return line[:160]
    found = next((block.text for block in blocks(text) if block.kind == "heading"), "")
    return _plain_inline(found).strip(" *#")


def bluf_text(text: str) -> str:
    """The whole bottom line: the "BLUF:" paragraph, or the paragraph under a BLUF/Summary heading."""
    items = blocks(text)
    for index, block in enumerate(items):
        plain = _plain_inline(block.text)
        found = re.match(r"^(?:BLUF|bottom\s+line(?:\s+up\s+front)?)\s*[:\-–—]\s*(.+)", plain, re.IGNORECASE)
        if found:
            return found.group(1)
        if block.kind == "heading" and re.match(r"^(?:BLUF|bottom\s+line|summary)", plain, re.IGNORECASE):
            following = next((item for item in items[index + 1:] if item.kind in ("paragraph", "bullet")), None)
            if following:
                return _plain_inline(following.text)
    return ""


def bottom_line(text: str) -> str:
    """The BLUF's first sentence ("**BLUF:** ...", a "Bottom line" heading's paragraph), else the
    first paragraph's."""
    items = blocks(text)
    for index, block in enumerate(items):
        plain = _plain_inline(block.text)
        found = re.match(r"^(?:BLUF|bottom\s+line(?:\s+up\s+front)?)\s*[:\-–—]\s*(.+)", plain, re.IGNORECASE)
        if found:
            return _SENTENCE_END.split(found.group(1), maxsplit=1)[0]
        if block.kind == "heading" and re.match(r"^(?:BLUF|bottom\s+line|summary)", plain, re.IGNORECASE):
            following = next((item for item in items[index + 1:] if item.kind in ("paragraph", "bullet")), None)
            if following:
                return _SENTENCE_END.split(_plain_inline(following.text), maxsplit=1)[0]
    first = next((block for block in items if block.kind == "paragraph"), None)
    return _SENTENCE_END.split(_plain_inline(first.text), maxsplit=1)[0] if first else ""


def js8_line(text: str, callsign: str = "") -> str:
    """A short JS8Call message (at most JS8CALL_TARGET characters): DTG, precedence, the title, and
    the bottom line's first sentence, or its first clause, when it fits."""
    precedence = stated_precedence(text).split(" ")[0]
    head = " ".join(part for part in ("MAGNET S2", stated_dtg(text), precedence, title(text)) if part)
    tail = f" DE {callsign.upper()}" if callsign else ""
    first = bottom_line(text)
    candidates = [first, re.split(r"\s*[;:—–]\s+|,\s+(?:and|but|while)\s+", first, maxsplit=1)[0]] if first else []
    out = head
    for candidate in candidates:
        if candidate and len(ascii_upper(f"{head}: {candidate}{tail}")) <= JS8CALL_TARGET:
            out = f"{head}: {candidate.rstrip('.')}"
            break
    if len(ascii_upper(out + tail)) > JS8CALL_TARGET:
        out = out[:JS8CALL_TARGET - len(tail) - 3].rsplit(" ", 1)[0] + "..."
    return JS8CALL_UNSAFE.sub("", ascii_upper(out + tail)).strip()


# -- Word ---------------------------------------------------------------------------------

def write_docx(text: str, path) -> None:
    from docx import Document
    from docx.shared import Pt, RGBColor

    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    def runs(paragraph, words: str) -> None:
        for piece, bold, link in spans(words):
            run = paragraph.add_run(piece)
            run.bold = bold or None
            if link:
                run.font.color.rgb = RGBColor(0x0B, 0x5C, 0xAD)
                run.underline = True

    for block in blocks(text):
        if block.kind == "heading":
            runs(document.add_heading(level=min(block.level, 4)), block.text)
        elif block.kind == "paragraph":
            runs(document.add_paragraph(), block.text)
        elif block.kind == "bullet":
            runs(document.add_paragraph(style="List Bullet"), block.text)
        elif block.kind == "numbered":
            runs(document.add_paragraph(style="List Number"), block.text)
        elif block.kind == "table" and block.rows:
            columns = max(len(row) for row in block.rows)
            table = document.add_table(rows=len(block.rows), cols=columns)
            table.style = "Table Grid"
            for row_index, row in enumerate(block.rows):
                for column, cell_text in enumerate(row):
                    cell = table.cell(row_index, column)
                    cell.text = ""
                    runs(cell.paragraphs[0], cell_text)
                    if row_index == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            document.add_paragraph()
        elif block.kind == "rule":
            document.add_paragraph()
    document.save(path)


# -- web page -----------------------------------------------------------------------------

def _html_inline(text: str) -> str:
    out = []
    for words, bold, link in spans(text):
        piece = escape(words)
        if link:
            piece = f'<a href="{escape(link)}">{piece}</a>'
        out.append(f"<strong>{piece}</strong>" if bold else piece)
    return "".join(out)


def render_html(text: str) -> str:
    body: list[str] = []
    open_list = ""
    for block in blocks(text):
        wanted = {"bullet": "ul", "numbered": "ol"}.get(block.kind, "")
        if open_list and wanted != open_list:
            body.append(f"</{open_list}>")
            open_list = ""
        if wanted and not open_list:
            body.append(f"<{wanted}>")
            open_list = wanted
        if block.kind == "heading":
            level = min(block.level, 4)
            body.append(f"<h{level}>{_html_inline(block.text)}</h{level}>")
        elif block.kind == "paragraph":
            body.append(f"<p>{_html_inline(block.text)}</p>")
        elif wanted:
            body.append(f"<li>{_html_inline(block.text)}</li>")
        elif block.kind == "table" and block.rows:
            head, *rest = block.rows
            body.append("<div class=\"table\"><table><thead><tr>" + "".join(f"<th>{_html_inline(c)}</th>" for c in head)
                        + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_html_inline(c)}</td>" for c in row)
                                                            + "</tr>" for row in rest) + "</tbody></table></div>")
        elif block.kind == "rule":
            body.append("<hr>")
    if open_list:
        body.append(f"</{open_list}>")
    name = escape(title(text) or "S2 report")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
:root {{ color-scheme: light dark; --fg: #1b1f23; --bg: #ffffff; --line: #d0d7de; --muted: #57606a; --link: #0b5cad; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg: #e6edf3; --bg: #0d1117; --line: #30363d; --muted: #8b949e; --link: #58a6ff; }} }}
body {{ font: 16px/1.6 system-ui, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg); margin: 0; }}
main {{ max-width: 46rem; margin: 0 auto; padding: 2rem max(16px, env(safe-area-inset-left)); }}
h1, h2, h3, h4 {{ line-height: 1.25; }}
a {{ color: var(--link); }}
.table {{ overflow-x: auto; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ border: 1px solid var(--line); padding: .4rem .6rem; text-align: left; vertical-align: top; }}
hr {{ border: 0; border-top: 1px solid var(--line); }}
</style></head>
<body><main>
{chr(10).join(body)}
</main></body></html>
"""


def write_html(text: str, path) -> None:
    from pathlib import Path

    Path(path).write_text(render_html(text), encoding="utf-8")
