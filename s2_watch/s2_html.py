"""Write a MAGNET S2 Intelligence Report as a web page in the MAGNET HTML template's style."""

from __future__ import annotations

from html import escape

from s2_watch.s2_report import HEADER_FIELDS, HEADER_LABELS, TOP_BANNER, S2Report

STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow+Condensed:wght@400;600;700;800&family=Barlow:wght@400;500;600&display=swap');
:root{--navy:#0D1B2A;--blue-hdr:#1565C0;--red:#FF0000;--yellow:#FFFF00;--green:#1B5E20;--white:#FFFFFF;--off-white:#F5F7FA;
--border:#CCCCCC;--text:#111111;--muted:#607D8B;--mono:'Share Tech Mono',monospace;--cond:'Barlow Condensed',sans-serif;--body:'Barlow',sans-serif}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:var(--body);font-size:13px;color:var(--text);line-height:1.5;padding:24px 16px}
.page{max-width:900px;margin:0 auto;background:#fff;box-shadow:0 0 60px rgba(0,0,0,.6)}
.banner{background:var(--navy);color:var(--white);text-align:center;font-family:var(--cond);font-size:11px;font-weight:700;letter-spacing:.12em;padding:6px 16px;text-transform:uppercase}
.title-block{background:var(--navy);padding:28px 24px 22px;text-align:center;border-bottom:3px solid var(--blue-hdr)}
.title-block .org{font-family:var(--cond);font-size:30px;font-weight:800;color:var(--white);letter-spacing:.06em;text-transform:uppercase;line-height:1}
.title-block .subtitle{font-family:var(--cond);font-size:13px;font-weight:600;color:#90CAF9;letter-spacing:.18em;text-transform:uppercase;margin-top:6px}
.title-block .report-subject{font-family:var(--cond);font-size:16px;font-weight:700;color:#FFF;letter-spacing:.06em;margin-top:12px;padding:6px 20px;border-top:1px solid rgba(255,255,255,.2);border-bottom:1px solid rgba(255,255,255,.2)}
.title-block .meta{font-family:var(--mono);font-size:11px;color:#B0BEC5;margin-top:10px;letter-spacing:.04em}
.title-block .url{font-family:var(--mono);font-size:11px;color:#64B5F6;margin-top:4px;font-style:italic}
.body-content{padding:0 24px 24px}
.section-header{font-family:var(--cond);font-size:15px;font-weight:800;color:var(--blue-hdr);text-transform:uppercase;letter-spacing:.1em;padding:12px 0 6px;margin-top:20px;border-bottom:3px solid var(--blue-hdr);margin-bottom:10px}
.sub-header{font-family:var(--cond);font-size:13px;font-weight:700;color:var(--navy);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 6px;padding-left:4px;border-left:4px solid var(--navy)}
.body-content p{font-size:12.5px;line-height:1.6;margin-bottom:10px;color:var(--text)}
.body-content p.note{font-size:12px;color:var(--muted);font-style:italic}
.meta-table,.timeline-table,.prob-table{width:100%;border-collapse:collapse;font-size:12px}
.meta-table{margin-bottom:8px}.meta-table td{padding:6px 10px;border:1px solid var(--border);vertical-align:top;line-height:1.45}
.meta-table td.meta-label{background:var(--navy);color:var(--white);font-family:var(--cond);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.07em;width:20%;white-space:nowrap}
.meta-table td.meta-value{background:var(--off-white);color:var(--text)}
.timeline-table{margin:8px 0 12px}.timeline-table th,.prob-table th{background:var(--navy);color:var(--white);font-family:var(--cond);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.08em;padding:7px 10px;border:1px solid #444;text-align:left}
.timeline-table td{padding:6px 10px;border:1px solid var(--border);vertical-align:middle;line-height:1.45}
.timeline-table td.tl-time{background:var(--navy);color:var(--white);font-family:var(--mono);font-size:11px;font-weight:600;width:16%;text-align:center;white-space:nowrap}
.timeline-table td.tl-event{background:var(--off-white);font-size:12px}
.prob-table{margin:8px 0}.prob-table th:last-child{text-align:center;width:20%}
.prob-table td{padding:7px 10px;border:1px solid var(--border);vertical-align:middle}
.prob-table td.prob-cat{background:var(--off-white);font-family:var(--cond);font-weight:700;font-size:12.5px}
.prob-table td.prob-level{text-align:center;font-family:var(--cond);font-weight:800;font-size:12px;letter-spacing:.06em;text-transform:uppercase}
.lvl-high{background:var(--red);color:#000}.lvl-moderate{background:var(--yellow);color:#000}.lvl-low{background:var(--green);color:#fff}
.risk-list,.guidance-list,.source-list{list-style:none;padding:0;margin:4px 0 10px}
.risk-list li{padding:4px 0 4px 18px;position:relative;font-size:12.5px;line-height:1.5;border-bottom:1px solid #f0f0f0}
.risk-list li::before{content:'\\25B8';position:absolute;left:2px;color:var(--blue-hdr);font-size:11px;top:5px}
.guidance-list li{padding:5px 8px 5px 22px;position:relative;font-size:12.5px;line-height:1.5;border-left:3px solid var(--blue-hdr);margin-bottom:5px;background:#F8FAFF}
.guidance-list li::before{content:'\\2192';position:absolute;left:6px;color:var(--blue-hdr);font-weight:700}
.source-list li{padding:4px 0;font-family:var(--mono);font-size:10.5px;color:var(--muted);border-bottom:1px solid #f0f0f0;line-height:1.5}
.source-list .src-num{font-weight:700;color:var(--text);margin-right:6px}.source-list .src-pub{font-weight:700;color:var(--navy);margin-right:4px}
.source-list a{color:var(--blue-hdr);text-decoration:none}
.closing{text-align:center;font-family:var(--mono);font-size:11px;color:var(--muted);padding:16px 0 8px;border-top:1px solid #ddd;margin-top:16px}
.closing a{color:var(--blue-hdr)}
@media print{body{background:#fff;padding:0}.page{box-shadow:none;max-width:100%}}
"""


def _list_class(section: str) -> str:
    return "guidance-list" if section == "MITIGATION RECOMMENDATIONS" else "risk-list"


def render_html(report: S2Report) -> str:
    e = escape
    focus = report.fields.get("GEOGRAPHIC FOCUS", "")
    out = [f"<!DOCTYPE html>\n<html lang='en'>\n<head>\n<meta charset='UTF-8'>\n"
           f"<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
           f"<meta name='keywords' content='{e(', '.join(report.tags))}'>\n"
           f"<title>MAGNET S2 INTELLIGENCE REPORT — {e(report.subject)} — {e(report.dtg)}</title>\n"
           f"<style>{STYLE}</style>\n</head>\n<body>\n<div class='page'>\n"
           f"<div class='banner'>{e(TOP_BANNER)}</div>\n"
           f"<div class='title-block'><div class='org'>MAGNET S2</div><div class='subtitle'>Intelligence Report</div>"
           f"<div class='report-subject'>{e(report.subject)}</div>"
           f"<div class='meta'>DTG: {e(report.dtg)} &nbsp;|&nbsp; Geographic Focus: {e(focus)} &nbsp;|&nbsp; "
           f"Precedence: {e(report.fields.get('PRECEDENCE', ''))}</div><div class='url'>www.magnethf.com</div></div>\n"
           f"<div class='body-content'>\n<div class='section-header'>Report Identification</div>\n<table class='meta-table'>"]
    for key in HEADER_FIELDS:
        if key in report.fields:
            out.append(f"<tr><td class='meta-label'>{e(HEADER_LABELS[key])}</td>"
                       f"<td class='meta-value'>{e(report.fields[key])}</td></tr>")
    out.append("</table>")
    for name, blocks in report.sections:
        out.append(f"<div class='section-header'>{e(name.title() if name != 'SUMMARY (BLUF)' else 'Summary (BLUF)')}</div>")
        for block in blocks:
            if block.kind == "subheader":
                out.append(f"<div class='sub-header'>{e(block.text)}</div>")
            elif block.kind == "paragraph":
                out.append(f"<p>{e(block.text)}</p>")
            elif block.kind in ("note", "prompt"):
                out.append(f"<p class='note'>{e(block.text)}</p>")
            elif block.kind == "bullets":
                out.append(f"<ul class='{_list_class(name)}'>" + "".join(f"<li>{e(item)}</li>" for item in block.rows) + "</ul>")
            elif block.kind == "timeline":
                out.append("<table class='timeline-table'><thead><tr><th>TIME (ZULU)</th><th>EVENT</th></tr></thead><tbody>"
                           + "".join(f"<tr><td class='tl-time'>{e(when)}</td><td class='tl-event'>{e(event)}</td></tr>"
                                     for when, event in block.rows) + "</tbody></table>")
            elif block.kind == "probability":
                out.append("<table class='prob-table'><thead><tr><th>Assessment Category</th><th>Probability</th></tr></thead><tbody>"
                           + "".join(f"<tr><td class='prob-cat'>{e(category)}</td>"
                                     f"<td class='prob-level lvl-{e(level.lower())}'>{e(level.upper())}</td></tr>"
                                     for category, level in block.rows) + "</tbody></table>")
            elif block.kind == "sources":
                items = []
                for number, source, rating, description, url in block.rows:
                    link = f" <a href='{e(url)}' target='_blank'>{e(url.split('://', 1)[-1])}</a>" if url else ""
                    items.append(f"<li><span class='src-num'>[{e(number)}]</span><span class='src-pub'>{e(source)}"
                                 f"{(' ' + e(rating)) if rating else ''} —</span>{e(description)}{link}</li>")
                out.append("<ul class='source-list'>" + "".join(items) + "</ul>")
    out.append("<div class='closing'>Submit reports through established MAGNET situational awareness channels.<br>"
               "To Learn More About MAGNET, Visit <a href='https://www.magnethf.com' target='_blank'>www.MAGNETHF.COM</a></div>")
    out.append(f"</div>\n<div class='banner'>CLASSIFICATION: UNCLASSIFIED // OSINT &nbsp;|&nbsp; PREPARED BY: MAGNET S2 "
               f"OSINT TEAM &nbsp;|&nbsp; DTG: {e(report.dtg)} &nbsp;|&nbsp; www.magnethf.com</div>\n</div>\n</body>\n</html>\n")
    return "\n".join(out)


def write_html(report: S2Report, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_html(report))
