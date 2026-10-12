"""What an event is about: shown beside the events list when an event is selected.

The event's headline facts (category, area, urgency, when, how many reports from where),
whether an official source confirms it and how far ahead radio was, the operator's labels,
then every report in time order with its full text, its own confirmation and a link for
official and news items. Buttons act on the event.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from s2_watch.analysis.ollama import readable_text
from s2_watch.models import Message

STATUS_LABELS = {"interest": "Of interest", "followup": "Follow-up created", "ignored": "Ignored"}


def useful_headline(headline: str, body: str) -> str:
    """The analyzer's "what — where" headline, or "" when the text already says what happened."""
    headline = headline.replace(" — Coordinates in report", "").replace("Coordinates in report", "").strip(" —")
    what = headline.split(" — ")[0].strip().lower()
    return "" if not what or what in body.lower() else headline


class EventDetail(QWidget):
    show_on_map = Signal(int)
    draft_followup = Signal(int)
    mark = Signal(int, str)        # event id, status ("" clears)
    edit_note = Signal(int)
    show_reports = Signal(int)

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.current = None
        self._shown_id = self._shown_html = None
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.map_button = QPushButton("Show on map")
        self.map_button.clicked.connect(lambda: self.current and self.show_on_map.emit(self.current.event_id))
        self.draft_button = QPushButton("Draft follow-up…")
        self.draft_button.setToolTip("A MAGNET S2 report on this event, from its newest radio report")
        self.draft_button.clicked.connect(lambda: self.current and self.draft_followup.emit(self.current.event_id))
        self.label_button = QToolButton()
        self.label_button.setText("Label")
        self.label_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.label_button)
        for status, label in (("interest", "★ Of interest"), ("followup", "↪ Follow-up created"),
                              ("ignored", "Ignored"), ("", "Clear label")):
            menu.addAction(label).triggered.connect(
                lambda _checked=False, status=status: self.current and self.mark.emit(self.current.event_id, status))
        menu.addAction("Edit note…").triggered.connect(lambda: self.current and self.edit_note.emit(self.current.event_id))
        self.label_button.setMenu(menu)
        self.reports_button = QPushButton("Show its reports")
        self.reports_button.clicked.connect(lambda: self.current and self.show_reports.emit(self.current.event_id))
        buttons = QHBoxLayout()
        for button in (self.map_button, self.draft_button, self.label_button, self.reports_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browser)
        layout.addLayout(buttons)
        self.show_event(None)

    def show_event(self, event) -> None:
        """Show this event (an EventSummary), or the hint when none is selected."""
        self.current = event
        if event is None:
            self._shown_id = self._shown_html = None
        for button in (self.map_button, self.draft_button, self.label_button, self.reports_button):
            button.setEnabled(event is not None)
        if event is None:
            self.browser.setHtml("<p style='color:gray'>Select an event to see what it is about: its reports, "
                                 "their full text, official confirmation and how far ahead radio was.</p>")
            return
        self.map_button.setEnabled(bool(event.points))
        reports = self.store.event_reports(event.event_id)
        self.draft_button.setEnabled(bool(reports))
        html = self.render(event, reports)
        if event.event_id == self._shown_id and html == self._shown_html:
            return                       # a refresh with nothing new: keep the reader's place
        scroll = self.browser.verticalScrollBar()
        position = scroll.value() if event.event_id == self._shown_id else 0
        self.browser.setHtml(html)
        scroll.setValue(position)
        self._shown_id, self._shown_html = event.event_id, html

    def render(self, event, reports: list[dict]) -> str:
        e = escape
        area = ", ".join(part for part in (event.state, f"MARS Region {event.region}" if event.region else "") if part)
        urgency_colour = {"high": "#c62828", "medium": "#e65100"}.get(event.urgency, "gray")
        sources = ", ".join(event.sources)
        when = (self.format_time(event.first_seen) if event.first_seen == event.last_seen
                else f"{self.format_time(event.first_seen)} to {self.format_time(event.last_seen)}")
        out = [f"<h3 style='margin-bottom:2px'>{e(event.title)}</h3>",
               f"<p style='margin-top:0'><b>{e(event.category)}</b>"
               f"{' · ' + e(area) if area else ''} · <b style='color:{urgency_colour}'>{e(event.urgency.upper())}</b><br>"
               f"{e(when)} · {event.reports} report{'s' if event.reports != 1 else ''} from {e(sources)}</p>"]
        if event.official:
            lead = (f" Radio reported it <b>{event.radio_lead_minutes} minutes</b> before the official source."
                    if event.radio_lead_minutes else "")
            out.append(f"<p>✓ <b>Confirmed</b> by {e(event.official)}.{lead}</p>")
        else:
            out.append("<p><b>Not yet confirmed</b> by an official source (NWS, USGS, SPC, NIFC, FEMA, FAA, "
                       "State Department).</p>")
        labels = [(r["status"], r.get("status_note") or "") for r in reports if r["status"]]
        if labels:
            seen = []
            for status, note in labels:
                words = STATUS_LABELS.get(status, status) + (f": {note}" if note else "")
                if words not in seen:
                    seen.append(words)
            out.append(f"<p><b>Your labels:</b> {e('; '.join(seen))}</p>")
        out.append("<hr><p style='margin-bottom:4px'><b>Reports, in the order heard</b></p>")
        for report in reports:
            body = " ".join(readable_text(Message(report["source"], report["text"] or "")).split())
            sender = f" ({report['sender']})" if report.get("sender") else ""
            copies = f" · {report['copies']} copies via {report['sources']}" if report.get("copies", 1) > 1 else ""
            label = STATUS_LABELS.get(report["status"], "")
            headline = useful_headline(report.get("summary") or "", body)
            url = (report.get("fields") or {}).get("url", "")
            parts = [f"<p style='margin-top:8px'><b>{e(self.format_time(report['first_seen']))}</b> — "
                     f"{e(report['source'])}{e(sender)} · {e(report['urgency'])}{e(copies)}"
                     + (f" · <i>{e(label)}</i>" if label else "")]
            if headline:
                parts.append(f"<br><b>{e(headline)}</b>")
            parts.append(f"<br>{e(body)}")
            if report.get("corroboration"):
                parts.append(f"<br><span style='color:gray'>{e(report['corroboration'])}</span>")
            if url:
                parts.append(f"<br><a href='{e(url)}'>{e(url)}</a>")
            out.append("".join(parts) + "</p>")
        return "\n".join(out)
