"""The watch forms S2 Watch provides: one unfiltered form per source, one per category."""

from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS
from s2_watch.models import ANY_APPLICATION, WatchForm
from s2_watch.storage import SQLiteStore

DEFAULT_WATCH_FORMS = {
    "JS8Call": "All JS8Call messages",
    "MagCommstat": "All MagCommstat messages",
    "Commstat": "All Commstat messages",
    "FLMSG": "All FLMSG messages",
    "JS8SuperSpotter": "All JS8SuperSpotter messages",
    "APRS": "All APRS bulletins",
    "OSINT": "All OSINT reports",
}
# Bump to offer category forms added in a later release; forms the user deleted after an
# earlier installation are not recreated.
CATEGORY_FORMS_VERSION = 1


def install_default_forms(store: SQLiteStore) -> None:
    """Create any missing default forms (disabled); existing forms are left as they are."""
    existing = {(form.application, form.name) for form in store.list_watch_forms()}
    if int(store.get_setting("forms.category_version", "0") or 0) < CATEGORY_FORMS_VERSION:
        for category, pattern in CATEGORY_PATTERNS.items():
            if (ANY_APPLICATION, category) not in existing:
                store.save_watch_form(WatchForm(ANY_APPLICATION, category, pattern, enabled=False, use_regex=True))
        store.set_setting("forms.category_version", str(CATEGORY_FORMS_VERSION))
    for application, name in DEFAULT_WATCH_FORMS.items():
        if (application, name) not in existing:
            store.save_watch_form(WatchForm(application, name, "", enabled=False))
