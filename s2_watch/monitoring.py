import hashlib
from collections.abc import Callable, Iterable

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
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
                matching_form = next((form for form in self.forms if form.matches(message)), None)
                if matching_form is None:
                    continue
                matched = Message(
                    source_application=message.source_application,
                    raw_content=message.raw_content,
                    received_at=message.received_at,
                    form_name=matching_form.name,
                    fields=message.fields,
                )
                # A message read again (a re-scan or a restart) keeps its original row.
                message_id = self.store.save_message(matched, message_key(message))
                self.on_match(matched)
                result = None
                if self.analyzer:
                    result = self.analyzer.analyze(matched)
                    self.store.save_analysis(message_id, result)
                if self.on_saved:
                    self.on_saved(message_id, matched, result)
                if result is not None and self.on_analysis:
                    self.on_analysis(matched, result)
