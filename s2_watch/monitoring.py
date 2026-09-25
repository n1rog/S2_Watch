from collections.abc import Callable, Iterable

from s2_watch.adapters.base import ApplicationAdapter
from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.storage import SQLiteStore


def message_key(message: Message) -> tuple:
    """Identity of a source message across re-reads (FLMSG files have no timestamp)."""
    identity = (
        message.fields.get("message_id") or message.fields.get("filename")
        or message.received_at.isoformat()
    )
    return (message.source_application, message.fields.get("type", ""), identity, message.raw_content)


class Monitor:
    def __init__(
        self,
        adapters: Iterable[ApplicationAdapter],
        forms: Iterable[WatchForm],
        store: SQLiteStore,
        on_match: Callable[[Message], None],
        analyzer: NewsAnalyzer | None = None,
        on_analysis: Callable[[Message, AnalysisResult], None] | None = None,
        saved_keys: set | None = None,
    ) -> None:
        self.adapters = list(adapters)
        self.forms = list(forms)
        self.store = store
        self.on_match = on_match
        self.analyzer = analyzer
        self.on_analysis = on_analysis
        # Set from another thread to abandon a long poll (e.g. a large backlog) promptly.
        self.cancelled = False
        # Messages already saved; a re-scan of the review window shows them again
        # without saving duplicates.
        self.saved_keys = saved_keys

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
                key = message_key(message)
                if self.saved_keys is not None and key in self.saved_keys:
                    message_id = None
                else:
                    message_id = self.store.save_message(matched)
                    if self.saved_keys is not None:
                        self.saved_keys.add(key)
                self.on_match(matched)
                if self.analyzer:
                    result = self.analyzer.analyze(matched)
                    if message_id is not None:
                        self.store.save_analysis(message_id, result)
                    if self.on_analysis:
                        self.on_analysis(matched, result)
