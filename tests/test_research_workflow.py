"""S2 Watch as the start and end of a report researched elsewhere: the research packet out, the
finished report back in (Reports → New Report from Text…)."""

from datetime import datetime, timedelta, timezone

from s2_watch.pasted_report import blocks, is_pasted, js8_line, plain_text, render_html, title, write_docx

REPORT = """# Nationwide FAA Ground Stop — 13 Nov 2026

**BLUF:** The FAA halted departures nationwide for about three hours on 13 Nov after an equipment failure during a
telecoms upgrade. Very likely (80–95%) no hostile cause.

## Key facts
- 13 Nov 14:10Z: FAA issues a nationwide ground stop [1]
- K6UDA reports cargo aircraft moving again at 02:32Z [2]

## Sources
| # | Source | Kind |
|---|--------|------|
| 1 | FAA statement | official |
| 2 | K6UDA StatRep | radio |

See [the FAA notice](https://www.faa.gov/newsroom) for details.
"""


def test_a_pasted_report_keeps_its_structure_in_every_format(tmp_path) -> None:
    from docx import Document

    assert is_pasted(REPORT) and not is_pasted("SUBJECT: Flooding\nDTG: 261112-1800Z")
    kinds = [block.kind for block in blocks(REPORT)]
    assert kinds[:2] == ["heading", "paragraph"] and kinds.count("bullet") == 2 and "table" in kinds
    assert title(REPORT) == "Nationwide FAA Ground Stop — 13 Nov 2026"
    plain = plain_text(REPORT)
    assert "**" not in plain and "#" not in plain.splitlines()[0] and "the FAA notice (https://www.faa.gov/newsroom)" in plain
    line = js8_line(REPORT, "n1rog")
    assert line.startswith("MAGNET S2 NATIONWIDE FAA GROUND STOP") and line.endswith("DE N1ROG") and len(line) <= 200
    html = render_html(REPORT)
    assert "<h1>" in html and "<strong>BLUF:</strong>" in html and "<table>" in html and 'href="https://www.faa.gov/newsroom"' in html
    write_docx(REPORT, tmp_path / "r.docx")
    document = Document(tmp_path / "r.docx")
    assert document.paragraphs[0].style.name == "Heading 1" and len(document.tables) == 1
    assert any(run.bold and run.text == "BLUF:" for paragraph in document.paragraphs for run in paragraph.runs)


def test_new_report_from_text_saves_every_format_and_lists_it(main_window, tmp_path) -> None:
    from s2_watch import report_files

    window = main_window()
    window.store.set_setting(report_files.FOLDER_SETTING, str(tmp_path / "reports"))
    dialog = window.new_report_from_text(text=REPORT, show=False)
    assert dialog.js8call.toPlainText().startswith("MAGNET S2 NATIONWIDE FAA GROUND STOP")
    assert "**" not in dialog.flmsg.toPlainText()
    dialog.save_all()
    written = sorted(path.name for path in (tmp_path / "reports").rglob("*") if path.is_file())
    assert len(written) == 5 and all(name.startswith("MAGNET_S2_NATIONWIDE_FAA_GROUND_STOP_") for name in written)
    assert {name.rsplit(".", 1)[1] for name in written} == {"txt", "docx", "html"}
    [draft] = window.store.list_drafts()
    assert draft["subject"] == "Nationwide FAA Ground Stop — 13 Nov 2026" and draft["dtg"].endswith("Z")


def test_the_research_packet_numbers_the_reports_as_the_panel_does(main_window) -> None:
    from PySide6.QtWidgets import QApplication

    from tests.test_navigation import add_event

    window = main_window()
    when = datetime.now(timezone.utc) - timedelta(hours=2)
    _m, _r, _report, event_id = add_event(window.store, "Water main break in Pickens", when, official=True)
    text = window.copy_research_packet(event_id)
    assert QApplication.clipboard().text() == text
    assert text.startswith("S2 WATCH RESEARCH PACKET: Water main break")
    assert "[1] " in text and "Official confirmation: NWS" in text and "Cite these reports by their number" in text
    assert window.packet_event[0] == event_id
    dialog = window.new_report_from_text(text=REPORT, show=False)
    assert dialog.event_id == event_id and "Water main break" in dialog.windowTitle()
