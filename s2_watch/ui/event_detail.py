"""What an event is about: shown beside the events list when an event is selected.

The event's headline facts (category, area, urgency, when, how many reports from where),
whether an official source confirms it and how far ahead radio was, the operator's labels,
then every report in time order with its full text, its own confirmation and a link for
official and news items. Buttons act on the event.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from html import escape

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from s2_watch.analysis.ollama import readable_text
from s2_watch.event_assessment import TREND_MARKS, changes
from s2_watch.events import quiet_hours
from s2_watch.models import Message
from s2_watch.senders import GRADE_WORDS, callsign
from s2_watch.ui.metrics_view import is_dark

STATUS_LABELS = {"interest": "Of interest", "followup": "Follow-up created", "ignored": "Ignored"}
def _when(value) -> datetime:
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


PLACE_WORDS = {"precise": "exact point", "city": "town", "county": "county", "grid": "grid square"}


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
    close_requested = Signal(int)
    fix_location = Signal(int)     # report id
    reopen_requested = Signal(int)
    assessment_done = Signal(int, str, str)     # event id, assessment JSON, error (from a thread)
    assessment_changed = Signal(int)            # an assessment was accepted (event id)

    def __init__(self, store, format_time, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.format_time = format_time
        self.current = None
        self.quiet_for = None          # event -> quiet coverage cells near it (s2_watch.silence)
        # Set by the main window: event -> (prompt, report count); () -> (endpoint, model).
        self.assessment_brief = None
        self.ollama_settings = None
        self._assessing: dict[int, str] = {}       # event id -> model, while Ollama works
        self._assessment_errors: dict[int, str] = {}
        self.assessment_done.connect(self._assessed)
        self._shown_id = self._shown_html = None
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)          # web links open in the browser; s2watch: links act here
        self.browser.anchorClicked.connect(self._link)
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
        self.close_button = QPushButton("Close event…")
        self.close_button.setToolTip("It's over: show it as closed, and list it as resolved in briefings. "
                                     "A new report reopens it.")
        self.close_button.clicked.connect(self._close_or_reopen)
        self.assess_button = QPushButton("Assess (Ollama)")
        self.assess_button.setToolTip("Ask Ollama what this event's reports add up to: trend, agreement, likely "
                                      "developments, gaps and recommendations, citing the reports by number. "
                                      "Takes a minute or two; the event and its reports are not changed.")
        self.assess_button.clicked.connect(self.assess)
        buttons = QHBoxLayout()
        for button in (self.map_button, self.draft_button, self.assess_button, self.label_button, self.reports_button,
                       self.close_button):
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
        for button in (self.map_button, self.draft_button, self.assess_button, self.label_button, self.reports_button,
                       self.close_button):
            button.setEnabled(event is not None)
        self.close_button.setText("Reopen event" if event is not None and event.closed_at is not None
                                  else "Close event…")
        if event is None:
            self.browser.setHtml("<p style='color:gray'>Select an event to see what it is about: its reports, "
                                 "their full text, official confirmation and how far ahead radio was.</p>")
            return
        self.map_button.setEnabled(bool(event.points))
        reports = self.store.event_reports(event.event_id)
        self.draft_button.setEnabled(bool(reports))
        busy = event.event_id in self._assessing
        self.assess_button.setEnabled(bool(reports) and not busy and self.assessment_brief is not None)
        self.assess_button.setText("Assessing…" if busy else "Assess (Ollama)")
        html = self.render(event, reports, self.store.sender_track_records(),
                           assessment=self.store.event_assessment(event.event_id),
                           accepted=self.store.accepted_assessment(event.event_id),
                           versions=len(self.store.assessment_history(event.event_id)),
                           assessing=self._assessing.get(event.event_id, ""),
                           assessment_error=self._assessment_errors.get(event.event_id, ""))
        if event.event_id == self._shown_id and html == self._shown_html:
            return                       # a refresh with nothing new: keep the reader's place
        scroll = self.browser.verticalScrollBar()
        position = scroll.value() if event.event_id == self._shown_id else 0
        # Links readable in the current theme (the default blue is too dark on a dark one).
        self.browser.document().setDefaultStyleSheet(f"a {{ color: {'#8ab4f8' if is_dark(self) else '#1a5fb4'}; }}")
        self.browser.setHtml(html)
        scroll.setValue(position)
        self._shown_id, self._shown_html = event.event_id, html

    def assess(self) -> None:
        """Ask Ollama to assess the shown event, in the background."""
        event = self.current
        if event is None or self.assessment_brief is None or self.ollama_settings is None \
                or event.event_id in self._assessing:
            return
        from s2_watch.event_assessment import assess_event

        brief, count = self.assessment_brief(event)
        endpoint, model = self.ollama_settings()
        self._assessing[event.event_id] = model
        self._assessment_errors.pop(event.event_id, None)
        event_id = event.event_id

        def work() -> None:
            try:
                self.assessment_done.emit(event_id, assess_event(brief, count, endpoint, model).to_json(), "")
            except Exception as error:      # noqa: BLE001 - shown to the operator
                self.assessment_done.emit(event_id, "", str(error) or type(error).__name__)

        threading.Thread(target=work, daemon=True, name="s2-watch-event-assessment").start()
        self.show_event(event)

    def _assessed(self, event_id: int, body: str, error: str) -> None:
        from s2_watch.event_assessment import EventAssessment

        self._assessing.pop(event_id, None)
        assessment = EventAssessment.from_json(body) if body else None
        if assessment is not None:
            self.store.save_event_assessment(event_id, assessment)
        else:
            self._assessment_errors[event_id] = error or "no assessment"
        if self.current is not None and self.current.event_id == event_id:
            self.show_event(self.current)

    # -- reviewing assessments ----------------------------------------------------------

    def _review(self, assessment, heading: str = "") -> None:
        from s2_watch.ui.assessment_dialog import AssessmentDialog

        event = self.current
        analyst = self.store.get_setting("operator.callsign", "").upper()
        dialog = AssessmentDialog(event.title, assessment, event.reports, analyst, self, heading)
        if dialog.exec() and dialog.accepted is not None:
            self.store.accept_event_assessment(event.event_id, dialog.accepted)
            self.show_event(self.current)
            self.assessment_changed.emit(event.event_id)

    def review_proposal(self) -> None:
        proposed = self.store.event_assessment(self.current.event_id)
        if proposed is not None:
            self._review(proposed)

    def dismiss_proposal(self) -> None:
        self.store.dismiss_event_assessment(self.current.event_id)
        self.show_event(self.current)

    def edit_accepted(self) -> None:
        from dataclasses import replace

        accepted = self.store.accepted_assessment(self.current.event_id)
        if accepted is not None:      # edited now, against every report there is
            accepted = replace(accepted, reports=len(self.store.event_reports(self.current.event_id)))
            self._review(accepted, "Edit the event's assessment. Accepting saves a new version; the earlier one "
                                   "stays in its history.")

    def write_assessment(self) -> None:
        from s2_watch.event_assessment import EventAssessment

        self._review(EventAssessment(origin="analyst", model="",
                                     reports=len(self.store.event_reports(self.current.event_id))),
                     "Write the event's assessment. Cite the numbered reports in brackets, e.g. [2].")

    def show_history(self) -> None:
        from s2_watch.ui.assessment_dialog import AssessmentHistoryDialog

        AssessmentHistoryDialog(self.current.title, self.store.assessment_history(self.current.event_id),
                                self.format_time, self).exec()

    def _link(self, url: QUrl) -> None:
        if url.scheme() == "s2watch":
            action, _, value = url.path().partition("/")
            if action == "fix-location" and value.isdigit():
                self.fix_location.emit(int(value))
            elif value.isdigit() and self.current is not None and self.current.event_id == int(value):
                {"accept-assessment": self.review_proposal, "dismiss-assessment": self.dismiss_proposal,
                 "edit-assessment": self.edit_accepted, "write-assessment": self.write_assessment,
                 "assessment-history": self.show_history}.get(action, lambda: None)()
        else:
            QDesktopServices.openUrl(url)

    def _close_or_reopen(self) -> None:
        if self.current is None:
            return
        if self.current.closed_at is not None:
            self.reopen_requested.emit(self.current.event_id)
        else:
            self.close_requested.emit(self.current.event_id)

    def render(self, event, reports: list[dict], records: dict | None = None,
               now: datetime | None = None, assessment=None, assessing: str = "",
               assessment_error: str = "", accepted=None, versions: int = 0) -> str:
        e = escape
        records = records or {}
        now = now or datetime.now(timezone.utc)
        area = ", ".join(part for part in (event.state, f"MARS Region {event.region}" if event.region else "") if part)
        urgency_colour = {"high": "#c62828", "medium": "#e65100"}.get(event.urgency, "gray")
        sources = ", ".join(event.sources)
        when = (self.format_time(event.first_seen) if event.first_seen == event.last_seen
                else f"{self.format_time(event.first_seen)} to {self.format_time(event.last_seen)}")
        out = [f"<p style='color:#c62828;margin:0'><b>EXERCISE</b>: these reports are exercise traffic.</p>"
               if getattr(event, "exercise", False) else "",
               f"<h3 style='margin-bottom:2px'>{e(event.title)}</h3>",
               f"<p style='margin-top:0'><b>{e(event.category)}</b>"
               f"{' · ' + e(area) if area else ''} · <b style='color:{urgency_colour}'>{e(event.urgency.upper())}</b><br>"
               f"{e(when)} · {event.reports} report{'s' if event.reports != 1 else ''} from {e(sources)}</p>"]
        if event.closed_at is not None:
            who = "you" if event.closed_by == "operator" else "a report"
            out.append(f"<p style='color:#2e7d32'>✓ <b>Closed</b> {e(self.format_time(event.closed_at))} by {who}"
                       f"{': ' + e(event.close_reason) if event.close_reason else ''}. A new report reopens it.</p>")
        elif event.status(now) == "quiet":
            out.append(f"<p><b>Quiet:</b> no new reports for {quiet_hours(now - event.last_seen)} hours. "
                       "It may be over; close it if you know it is.</p>")
        if event.single_source(now):
            out.append(f"<p style='color:#e65100'>⚠ <b>Single source:</b> only one station or outlet has reported "
                       f"this, and nothing has confirmed it in {quiet_hours(now - event.first_seen)} hours.</p>")
        for cell in (self.quiet_for(event) if self.quiet_for else [])[:3]:
            stations = ", ".join(cell["stations"][:3])
            out.append(f"<p style='color:#e65100'>📡 <b>Radio silence nearby:</b> stations around {e(cell['grid'])}"
                       f"{' (' + e(stations) + ')' if stations else ''} usually send about {cell['usual_per_3h']} "
                       f"messages every 3 hours and haven't been heard for {cell['silent_hours']} hours.</p>")
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
        out.append(self.render_assessment(assessment, len(reports), assessing, assessment_error, accepted,
                                          event.event_id, versions))
        out.append(f"<hr><p style='margin-bottom:4px'><b>Reports, newest first</b> (numbered in the order heard)</p>"
                   if len(reports) > 1 else "<hr><p style='margin-bottom:4px'><b>Report</b></p>")
        numbers = {id(report): number for number, report in enumerate(reports, 1)}
        for report in sorted(reports, key=lambda r: _when(r["first_seen"]), reverse=True):
            body = " ".join(readable_text(Message(report["source"], report["text"] or "")).split())
            sender = f" ({report['sender']})" if report.get("sender") else ""
            station = callsign(report["source"], report.get("sender") or "", report.get("text") or "")
            record = records.get(station) if report["source"] != "OSINT" else None
            track = ""
            if record is not None:
                track = (f" · {'trust level' if record.trust else 'track record'} <b>{record.grade}</b> "
                         f"({e(GRADE_WORDS[record.grade])}: {e(record.explanation)})")
            stated = report.get("event_time") or ""
            if stated and abs((_when(report["first_seen"]) - _when(stated)).total_seconds()) >= 300:
                track += f" · time in message <b>{e(self.format_time(stated))}</b>"
            drill = {"exercise": " · <b style='color:#c62828'>EXERCISE</b>",
                     "test": " · <b>TEST</b>"}.get(report.get("exercise") or "", "")
            copies = f" · {report['copies']} copies via {report['sources']}" if report.get("copies", 1) > 1 else ""
            label = STATUS_LABELS.get(report["status"], "")
            headline = useful_headline(report.get("summary") or "", body)
            url = (report.get("fields") or {}).get("url", "")
            parts = [f"<p style='margin-top:8px'><b>[{numbers[id(report)]}] {e(self.format_time(report['first_seen']))}</b> — "
                     f"{e(report['source'])}{e(sender)} · {e(report['urgency'])}{e(copies)}"
                     + (f" · <i>{e(label)}</i>" if label else "") + track + drill]
            if headline:
                parts.append(f"<br><b>{e(headline)}</b>")
            parts.append(f"<br>{e(body)}")
            if report.get("corroboration"):
                parts.append(f"<br><span style='color:gray'>{e(report['corroboration'])}</span>")
            place = ", ".join(str(part) for part in (report.get("state"), f"MARS Region {report['region']}"
                                                     if report.get("region") else "") if part)
            level = PLACE_WORDS.get(report.get("place_level") or "", "")
            if report.get("lat") is not None and report.get("lon") is not None:
                # The point itself: at sea there may be no country to name.
                point = f"{float(report['lat']):.3f}, {float(report['lon']):.3f}"
                place = f"{place} ({point})" if place else point
            parts.append(f"<br><span style='color:gray'>Placed: {e(place) if place else 'no location'}"
                         f"{' — ' + level if level and place else ''}</span> "
                         f"<a href='s2watch:fix-location/{report['report_id']}'>fix location</a>")
            if url:
                parts.append(f"<br><a href='{e(url)}'>{e(url)}</a>")
            out.append("".join(parts) + "</p>")
        return "\n".join(out)

    def _assessment_body(self, assessment) -> list[str]:
        """An assessment's sections as HTML."""
        e = escape
        out = []
        if assessment.bluf:
            out.append(f"<p style='margin:4px 0'><b>BLUF:</b> {e(assessment.bluf)}</p>")
        trend_colour = {"escalating": "#c62828", "winding down": "#2e7d32"}.get(assessment.trend, "inherit")
        out.append(f"<p style='margin:4px 0'><b>Trend:</b> <b style='color:{trend_colour}'>"
                   f"{TREND_MARKS.get(assessment.trend, '')} {e(assessment.trend)}</b>"
                   + (f" — {e(assessment.trend_reason)}" if assessment.trend_reason else "")
                   + (f"<br><b>Agreement:</b> {e(assessment.agreement)}" if assessment.agreement else "") + "</p>")
        for paragraph in assessment.assessment:
            out.append(f"<p style='margin:4px 0'>{e(paragraph)}</p>")
        if assessment.probabilities:
            rows = "".join(f"<tr><td>{e(statement)}</td><td><b>{e(level)}</b></td></tr>"
                           for statement, level in assessment.probabilities)
            out.append(f"<p style='margin:4px 0 0'><b>Probability</b></p><table cellpadding='2'>{rows}</table>")
        for title, items in (("Intelligence gaps", assessment.gaps), ("Mitigation", assessment.mitigation),
                             ("MAGNET guidance", assessment.guidance)):
            if items:
                out.append(f"<p style='margin:4px 0 0'><b>{title}</b></p><ul style='margin-top:0'>"
                           + "".join(f"<li>{e(item)}</li>" for item in items) + "</ul>")
        return out

    def render_assessment(self, proposed, report_count: int, assessing: str = "", error: str = "",
                          accepted=None, event_id: int = 0, versions: int = 0) -> str:
        """The event's accepted assessment (the analyst's), then Ollama's proposal waiting for
        review, each apart from the facts."""
        e = escape
        out = []
        if accepted is not None:
            when = self.format_time(_when(accepted.accepted_at)) if accepted.accepted_at else ""
            out.append("<p style='margin:6px 0 0'><b>Analyst assessment</b> <span style='color:gray'>— "
                       f"{e(accepted.credit)}, {e(when)}, from {accepted.reports} report"
                       f"{'s' if accepted.reports != 1 else ''}.</span> "
                       f"<a href='s2watch:edit-assessment/{event_id}'>edit</a>"
                       + (f" · <a href='s2watch:assessment-history/{event_id}'>history ({versions})</a>"
                          if versions > 1 else "") + "</p>")
            stale = report_count - accepted.reports
            if stale > 0 and proposed is None and not assessing:
                out.append(f"<p style='color:#e65100;margin:2px 0'>⚠ <b>Out of date:</b> {stale} new report"
                           f"{'s' if stale != 1 else ''} since. Assess (Ollama) again for a proposed update, "
                           "or edit it.</p>")
            out += self._assessment_body(accepted)
        if assessing:
            out.append(f"<p style='color:#1565c0'>⏳ <b>Assessing with Ollama ({e(assessing)})…</b> this can take a "
                       "minute or two. You can keep working.</p>")
        if error:
            out.append(f"<p style='color:#c62828'>Ollama couldn't assess this event: {e(error)}</p>")
        if proposed is not None and not assessing:
            when = self.format_time(_when(proposed.created_at)) if proposed.created_at else ""
            kind = "Proposed update" if accepted is not None else "Proposed assessment"
            out.append("<div style='margin:6px 0'><p style='margin:0'>"
                       f"<b style='color:#1565c0'>{kind}</b> <span style='color:gray'>— Ollama suggestion "
                       f"({e(proposed.model)}), {e(when)}, from {proposed.reports} report"
                       f"{'s' if proposed.reports != 1 else ''}, <b>not yet accepted</b>. Numbers in brackets are "
                       f"the reports below.</span><br><a href='s2watch:accept-assessment/{event_id}'>Review &amp; "
                       f"accept…</a> · <a href='s2watch:dismiss-assessment/{event_id}'>Dismiss</a></p>")
            stale = report_count - proposed.reports
            if stale > 0:
                out.append(f"<p style='color:#e65100;margin:2px 0'>⚠ <b>Out of date:</b> {stale} new report"
                           f"{'s' if stale != 1 else ''} since. Click Assess (Ollama) again.</p>")
            if accepted is not None:
                changed = changes(accepted, proposed)
                out.append("<p style='margin:4px 0'><b>What changed:</b> "
                           + (e("; ".join(changed)) if changed else "nothing new in trend, probabilities or gaps")
                           + "</p>")
            out += self._assessment_body(proposed)
            out.append("</div>")
        if accepted is None and proposed is None and not assessing and report_count:
            out.append(f"<p style='color:gray;margin:6px 0'>No assessment yet: Assess (Ollama), or "
                       f"<a href='s2watch:write-assessment/{event_id}'>write one</a>.</p>")
        if out:
            out.insert(0, "<div style='margin:6px 0'>")
            out.append("</div>")
        return "".join(out)
