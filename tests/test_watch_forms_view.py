from datetime import timedelta

from s2_watch.adapters import osint
from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.ui.watch_form_dialog import WatchFormDialog, pattern_words, words_pattern
from s2_watch.ui.watch_forms_view import WatchFormsPage, describe_form, form_group, pattern_keywords


def test_keyword_patterns_read_as_words() -> None:
    words = pattern_keywords(CATEGORY_PATTERNS["Crime"])
    assert words[:4] == ["shooting", "shooter", "shots fired", "gunfire"]
    assert "stabbed" in words                                  # stabb(?:ed|ing) reads as a word
    assert pattern_keywords(r"(?:\bflood(?:s|ing)?)|\bwashout") == ["flood", "washout"]   # operator keyword added
    form = WatchForm(ANY_APPLICATION, "Crime", CATEGORY_PATTERNS["Crime"], use_regex=True)
    assert describe_form(form).startswith("Keywords: shooting, shooter, shots fired")
    assert describe_form(WatchForm("JS8Call", "All JS8Call messages", "")) == "Every message"
    assert describe_form(WatchForm("OSINT", "Quakes", "", message_type="EARTHQUAKE")) == "Every message · type EARTHQUAKE"


def test_forms_are_grouped() -> None:
    assert form_group(WatchForm(ANY_APPLICATION, "Crime", "x", use_regex=True)) == "topics"
    assert form_group(WatchForm("JS8Call", "All JS8Call messages", "")) == "applications"
    assert form_group(WatchForm("JS8Call", "Bridges", "bridge")) == "custom"


def test_word_lists_round_trip_through_the_form_dialog() -> None:
    pattern = words_pattern(["bridge out", "road closed", "washout"])
    assert pattern_words(pattern) == ["bridge out", "road closed", "washout"]
    assert pattern_words(CATEGORY_PATTERNS["Crime"]) is None          # not a plain word list
    form = WatchForm("JS8Call", "Bridges", pattern, use_regex=True)
    assert form.matches(Message("JS8Call", "W1AW: BRIDGE OUT ON RT 9"))
    assert not form.matches(Message("JS8Call", "W1AW: THE BRIDGE IS OPEN"))
    dialog = WatchFormDialog(form=form)
    assert dialog.how.currentData() == "words"
    assert dialog.match_text.toPlainText() == "bridge out, road closed, washout"
    assert dialog.watch_form().match_text == pattern


def test_the_form_dialog_explains_what_is_missing() -> None:
    dialog = WatchFormDialog()
    dialog.name.setText("Bad")
    dialog.how.setCurrentIndex(dialog.how.findData("pattern"))
    dialog.match_text.setPlainText("(unclosed")
    dialog.accept()
    assert "doesn't work" in dialog.error.text() and dialog.result() == 0


def test_osint_sources_have_their_own_switches() -> None:
    page = WatchFormsPage({"usgs": ("USGS earthquakes", "every 10 min"), "gdelt": ("News", "every 15 min")}, {"usgs"})
    switched = []
    page.osint_source_toggled.connect(lambda key, on: switched.append((key, on)))
    assert page.osint_boxes["usgs"].isChecked() and not page.osint_boxes["gdelt"].isChecked()
    page.osint_boxes["gdelt"].setChecked(True)
    assert switched == [("gdelt", True)]
    page.set_osint_status({"usgs": "updated 14:02", "gdelt": "updated 14:05"})
    assert page.osint_status["usgs"].text() == "updated 14:02" and page.osint_status["gdelt"].text() == "updated 14:05"


def test_an_osint_adapter_with_no_sources_fetches_nothing() -> None:
    adapter = osint.OsintAdapter(timedelta(hours=1), {})
    try:
        assert adapter.status.startswith("Off") and adapter.read_messages() == []
    finally:
        adapter.close()


def test_the_watch_forms_menu_switches_osint_sources(tmp_path, main_window) -> None:
    window = main_window()
    action, box = window.osint_actions["state"], window.watch_forms_page.osint_boxes["state"]
    assert action.isChecked() and box.isChecked()
    action.trigger()                                        # off from the menu
    assert not box.isChecked() and window.store.get_setting("osint.source.state") == "0"
    assert "state" not in window.osint_adapter.fetchers
    box.setChecked(True)                                    # on from the page
    assert action.isChecked() and "state" in window.osint_adapter.fetchers
