"""The default forms: MagCommstat kept whole, category words kept up to date, no future dates."""

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS, EARLIER_PATTERNS
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.default_forms import install_default_forms
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.monitoring import message_key, not_in_future
from s2_watch.storage import SQLiteStore

SCREWWORM = ("QST US SCREWWORM SPREAD: US INFESTATIONS CONTINUE TO EXPAND IN THE SOUTHWEST. USDA DATA REPORTED "
             "NOV 5 SHOWS 116 CONFIRMED U.S. ANIMAL INFESTATIONS ACROSS NM  TX  & AZ  56 REMAIN ACTIVE.")


def form(store, application, name):
    return next(form for form in store.list_watch_forms(application) if form.name == name)


def test_animal_and_crop_health_is_health_news() -> None:
    assert re.search(CATEGORY_PATTERNS["Health / Wellness"], SCREWWORM, re.I)
    result = RuleBasedNewsAnalyzer().analyze(Message("MagCommstat", SCREWWORM, fields={"type": "STATREP"}))
    assert (result.newsworthy, result.category) == (True, "Health / Wellness")
    for text in ("AFRICAN SWINE FEVER CONFIRMED IN HOGS", "Rabid fox reported near the school",
                 "Chronic wasting disease found in deer"):
        assert re.search(CATEGORY_PATTERNS["Health / Wellness"], text, re.I), text
    for text in ("HERD OF ELK ON THE ROAD", "TICK TOCK NET CHECK-IN", "SCREWDRIVER ANTENNA AT 25 WATTS"):
        assert not re.search(CATEGORY_PATTERNS["Health / Wellness"], text, re.I), text


def test_every_magcommstat_message_is_kept_from_the_start(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    install_default_forms(store)
    assert form(store, "MagCommstat", "All MagCommstat messages").enabled
    assert not form(store, "JS8Call", "All JS8Call messages").enabled


def test_an_existing_installation_turns_magcommstat_on_once(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    store.save_watch_form(WatchForm("MagCommstat", "All MagCommstat messages", "", enabled=False))
    install_default_forms(store)
    magcommstat = form(store, "MagCommstat", "All MagCommstat messages")
    assert magcommstat.enabled
    store.save_watch_form(replace(magcommstat, enabled=False))      # the operator turns it off again
    install_default_forms(store)
    assert not form(store, "MagCommstat", "All MagCommstat messages").enabled


def test_an_update_brings_category_words_up_to_date_and_keeps_the_operators(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    install_default_forms(store)
    earlier = EARLIER_PATTERNS["Health / Wellness"][0]
    store.save_watch_form(replace(form(store, ANY_APPLICATION, "Health / Wellness"),
                                  match_text=f"(?:{earlier})|\\bmeningitis"))           # a word they added
    store.save_watch_form(replace(form(store, ANY_APPLICATION, "Crime"), match_text=r"\bpoaching\b"))  # rewritten
    install_default_forms(store)
    current = CATEGORY_PATTERNS["Health / Wellness"]
    assert form(store, ANY_APPLICATION, "Health / Wellness").match_text == f"(?:{current})|\\bmeningitis"
    assert form(store, ANY_APPLICATION, "Crime").match_text == r"\bpoaching\b"
    for category, patterns in EARLIER_PATTERNS.items():          # an update can't repeat itself
        assert all(pattern not in CATEGORY_PATTERNS[category] for pattern in patterns)


def test_a_source_dating_a_message_in_the_future_is_dated_when_read() -> None:
    now = datetime(2026, 10, 22, 17, 0, tzinfo=timezone.utc)
    typo = Message("OSINT", "State Dept travel advisory: French Polynesia",
                   received_at=datetime(2026, 12, 2, tzinfo=timezone.utc))
    fixed = not_in_future(typo, now)
    assert fixed.received_at == now and fixed.fields["source_time"] == "2026-12-02T00:00:00+00:00"
    fast_clock = Message("JS8Call", "CQ", received_at=now + timedelta(minutes=2))
    assert not_in_future(fast_clock, now) is fast_clock
    # A message without an id is still the same message when read again later.
    assert message_key(fixed) == message_key(not_in_future(typo, now + timedelta(hours=1)))


def test_records_saved_with_future_times_are_repaired(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    future = datetime.now(timezone.utc) + timedelta(days=27)
    message_id = store.save_message(Message("OSINT", "Advisory", received_at=future), "key")
    store.connection.execute("UPDATE messages SET received_at = ? WHERE id = ?", (future.isoformat(), message_id))
    store.connection.commit()
    store.connection.close()
    store = SQLiteStore(tmp_path / "s2.db")
    received = datetime.fromisoformat(store.connection.execute(
        "SELECT received_at FROM messages WHERE id = ?", (message_id,)).fetchone()[0])
    assert abs(received - datetime.now(timezone.utc)) < timedelta(minutes=1)
