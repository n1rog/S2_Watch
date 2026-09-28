import hashlib
from dataclasses import replace
from collections.abc import Callable, Iterable

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.digest import split_digest
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.storage import SQLiteStore


def message_key(message: Message) -> str:
    """Identity of a source message across re-reads and restarts (FLMSG files have no timestamp)."""
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
                    message_id = self.store.save_message(digest, message_key(message))
                    self.store.detach_from_report(message_id)  # an earlier version made it one report
                    self.store.set_form_name(message_id, "Digest")
                    self.on_match(digest)

    def _process(self, message: Message) -> bool:
        """Match, analyse, save and report one message; True if a watch form matched it."""
        matching = [form for form in self.forms if form.matches(message)]
        if not matching:
            return False
        matched = replace(message, form_name=matching[0].name)
        result = self.analyzer.analyze(matched) if self.analyzer else None
        if result is not None and result.newsworthy:
            # Credit the form named after the report's category (the category forms), not
            # simply the first form in the list that happened to match.
            same_category = next((form for form in matching if form.name == result.category), None)
            if same_category is not None:
                matched = replace(matched, form_name=same_category.name)
        # A message read again (a re-scan or a restart) keeps its original row.
        message_id = self.store.save_message(matched, message_key(message))
        self.on_match(matched)
        if result is not None:
            self.store.save_analysis(message_id, result)
        if self.on_saved:
            self.on_saved(message_id, matched, result)
        if result is not None and self.on_analysis:
            self.on_analysis(matched, result)
        return True
