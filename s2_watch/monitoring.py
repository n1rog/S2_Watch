import hashlib
from dataclasses import replace
from collections.abc import Callable, Iterable

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.adapters.analyst import ENTRY_TYPE
from s2_watch.digest import split_digest
from s2_watch.exercise import mark_exercise
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.storage import SQLiteStore


def analyzer_name(analyzer) -> str:
    """Identifies an analyzer's results ("rules", "ollama:llama3.2"...)."""
    return getattr(analyzer, "name", type(analyzer).__name__) if analyzer is not None else ""


def message_key(message: Message) -> str:
    """Identity of a source message across re-reads and restarts (FLMSG files have no timestamp).

    OSINT items carry the publisher's own event id; the publisher revises their text (USGS
    relocating a quake, an edited TFR), so the id alone identifies them.
    """
    if message.source_application == "OSINT" and message.fields.get("message_id"):
        parts = (message.source_application, message.fields.get("type", ""), message.fields["message_id"])
        return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    return legacy_message_key(message)


def legacy_message_key(message: Message) -> str:
    """The key used before 0.11.0 (it includes the text), to find messages saved then."""
    identity = (
        message.fields.get("message_id") or message.fields.get("filename")
        or message.received_at.isoformat()
    )
    parts = (message.source_application, message.fields.get("type", ""), identity, message.raw_content)
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


class Monitor:
    def __init__(
        self,
        adapters: Iterable[ApplicationAdapter],
        forms: Iterable[WatchForm],
        store: SQLiteStore,
        on_match: Callable[[Message], None],
        analyzer: NewsAnalyzer | None = None,
        on_analysis: Callable[[Message, AnalysisResult], None] | None = None,
        on_saved: Callable[[int, Message, AnalysisResult | None], None] | None = None,
        on_read: Callable[[Message], None] | None = None,
        on_unmatched: Callable[[Message], None] | None = None,
        exercise_in_progress: bool = False,
    ) -> None:
        self.adapters = list(adapters)
        self.forms = list(forms)
        self.store = store
        self.on_match = on_match
        self.analyzer = analyzer
        self.on_analysis = on_analysis
        # Set from another thread to abandon a long poll (e.g. a large backlog) promptly.
        self.cancelled = False
        # Called with each saved message's id, e.g. to link it to its report.
        self.on_saved = on_saved
        # Called with every message read, matched or not (e.g. for station coverage).
        self.on_read = on_read
        self.on_unmatched = on_unmatched   # messages no watch form kept
        # Exercise traffic counts as news only during an exercise (s2_watch.exercise).
        self.exercise_in_progress = exercise_in_progress

    def poll(self) -> None:
        active_applications = {
            form.application for form in self.forms if form.enabled
        }
        for adapter in self.adapters:
            adapter_application = getattr(adapter, "application_name", None)
            if (adapter_application and ANY_APPLICATION not in active_applications
                    and adapter_application not in active_applications):
                continue
            for message in adapter.read_messages():
                if self.cancelled:
                    return
                if self.on_read:
                    self.on_read(message)
                # A digest (weekly briefing, bulletin) is handled item by item; the whole
                # digest is kept for Live Monitor and History when any item matches.
                items = split_digest(message)
                if items is None:
                    self._process(message)
                    continue
                if sum(self._process(item) for item in items):
                    digest = replace(message, form_name="Digest", fields={**message.fields, "digest_parent": "1"})
                    message_id = self.store.save_message(digest, message_key(message), legacy_message_key(message))
                    digest = replace(digest, received_at=self.store.earliest_received_at(message_id, digest.received_at))
                    self.store.detach_from_report(message_id)  # an earlier version made it one report
                    self.store.set_form_name(message_id, "Digest")
                    self.on_match(digest)

    def _process(self, message: Message) -> bool:
        """Match, analyse, save and report one message; True if a watch form matched it."""
        matching = [form for form in self.forms if form.matches(message)]
        entry = message.fields.get("type") == ENTRY_TYPE and message.source_application == "OSINT"
        if entry and not matching:     # the operator's own entries are always kept
            matching = [WatchForm("OSINT", "Analyst entries", "")]
        if not matching:
            if self.on_unmatched:
                self.on_unmatched(message)
            return False
        matched = replace(message, form_name=matching[0].name)
        result = None
        if entry:   # the operator chose its category and urgency
            result = AnalysisResult(True, message.fields.get("category") or "routine",
                                    message.fields.get("urgency") or "medium",
                                    " ".join(message.raw_content.split())[:160], 1.0, ("analyst entry",))
        elif self.analyzer:
            # A message read again (every launch) reuses this analyzer's saved result: an AI
            # model takes seconds per message, so only new messages are sent to it.
            known = self.store.find_message_id(message_key(message), legacy_message_key(message))
            if known is not None:
                result = self.store.saved_analysis(known, analyzer_name(self.analyzer))
            if result is None:
                result = self.analyzer.analyze(matched)
        # The analyzer's own verdict is saved (and reused); exercise and test traffic is then
        # handled by the current exercise setting.
        analysis = result
        if result is not None:
            result = mark_exercise(matched, result, self.exercise_in_progress)
        if result is not None and result.newsworthy:
            # Credit the form named after the report's category (the category forms), not
            # simply the first form in the list that happened to match.
            same_category = next((form for form in matching if form.name == result.category), None)
            if same_category is not None:
                matched = replace(matched, form_name=same_category.name)
        # A message read again (a re-scan or a restart) keeps its original row.
        message_id = self.store.save_message(matched, message_key(message), legacy_message_key(message))
        # A message read again keeps the time it was first seen (sources without timestamps).
        matched = replace(matched, received_at=self.store.earliest_received_at(message_id, matched.received_at))
        self.on_match(matched)
        if analysis is not None:
            self.store.save_analysis(message_id, analysis, analyzer_name(self.analyzer))
        if self.on_saved:
            self.on_saved(message_id, matched, result)
        if result is not None and self.on_analysis:
            self.on_analysis(matched, result)
        return True
