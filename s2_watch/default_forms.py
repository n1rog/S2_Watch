"""The watch forms S2 Watch provides: one unfiltered form per source, one per category."""

from dataclasses import replace

from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS, EARLIER_PATTERNS
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
# Whole-application forms that start enabled. MagCommstat carries QSTs and StatReps meant as
# news, in words no topic form may know (a screwworm QST was dropped that way).
ENABLED_BY_DEFAULT = {"MagCommstat"}
# Bump to switch the ENABLED_BY_DEFAULT forms on once in existing installations; turning one
# off afterwards is respected.
ENABLED_BY_DEFAULT_VERSION = 1


def install_default_forms(store: SQLiteStore) -> None:
    """Create any missing default forms and bring the category forms' built-in words up to date."""
    existing = {(form.application, form.name) for form in store.list_watch_forms()}
    if int(store.get_setting("forms.category_version", "0") or 0) < CATEGORY_FORMS_VERSION:
        for category, pattern in CATEGORY_PATTERNS.items():
            if (ANY_APPLICATION, category) not in existing:
                store.save_watch_form(WatchForm(ANY_APPLICATION, category, pattern, enabled=False, use_regex=True))
        store.set_setting("forms.category_version", str(CATEGORY_FORMS_VERSION))
    switch_on = int(store.get_setting("forms.enabled_by_default_version", "0") or 0) < ENABLED_BY_DEFAULT_VERSION
    for application, name in DEFAULT_WATCH_FORMS.items():
        if (application, name) not in existing:
            store.save_watch_form(WatchForm(application, name, "", enabled=application in ENABLED_BY_DEFAULT))
        elif switch_on and application in ENABLED_BY_DEFAULT:
            form = next(form for form in store.list_watch_forms(application) if form.name == name)
            if not form.enabled:
                store.save_watch_form(replace(form, enabled=True))
    if switch_on:
        store.set_setting("forms.enabled_by_default_version", str(ENABLED_BY_DEFAULT_VERSION))
    update_category_forms(store)


def update_category_forms(store: SQLiteStore) -> None:
    """Put each category's current pattern in place of an earlier built-in one, keeping any
    words the operator added around it (a form they rewrote no longer contains it)."""
    for form in store.list_watch_forms(ANY_APPLICATION):
        current = CATEGORY_PATTERNS.get(form.name)
        for earlier in EARLIER_PATTERNS.get(form.name, ()):
            if current and earlier in form.match_text:
                store.save_watch_form(replace(form, match_text=form.match_text.replace(earlier, current)))
                break
