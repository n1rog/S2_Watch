"""Towns news headlines name without their state; the geocoder asked when it should be."""

import json

import pytest

from s2_watch.events import Place, clean_title, titled_with_place
from s2_watch.locating import LocationResolver, written_as_name
from s2_watch.models import Message


def place(text, **resolver):
    found = LocationResolver(**resolver).resolve(Message("OSINT", text, fields={"type": "NEWS", "sender": "example.com"}))
    return found[0].label if found else None


@pytest.mark.parametrize("text, label", [
    ("1 injured in shooting in East Providence", "East Providence, RI"),
    ("Burned-up switch causes power outage in much of North Platte", "North Platte, NE"),
    ("Oldsmar Walmart evacuated due to gas leak", "Oldsmar, FL"),
    ("Man pleads guilty to manslaughter in fatal Brownsville shooting", "Brownsville, TX"),   # the largest by far
    ("Man Pleads Guilty In Fatal Brownsville Shooting", "Brownsville, TX"),                  # Title Case
    ("Brownsville Police investigate stabbing", "Brownsville, TX"),
    ("Crew working to repair water main break in Pickens, city officials say", "Pickens, SC"),
    ("Suspect arrested for fatal shooting of 15-year-old Bronx boy", "Bronx, NY"),
])
def test_towns_named_without_their_state_are_placed(text, label) -> None:
    assert place(text) == label


@pytest.mark.parametrize("text", [
    "Justice Department sues over shooting",              # town names that are everyday words
    "In Surprise Move, Senate Passes Bill",
    "Mobile home fire kills two",
    "Eaton Fire survivors rally",                         # a named fire
    "Morgan Park football families protest",              # the name goes on
    "Gas leak alerts at West Broward High School",
    "Protest outside Elon Musk's home",
    "Update magnitude 7.4 earthquake near San Jose del Palmar.",
    "Power outage in Springfield",                         # many Springfields, none much the largest
    "Protest in Lebanon turns violent",                    # a country first
    "SHOOTING IN EAST PROVIDENCE",                         # all capitals: names can't be told from words
    "Teen charged for near-fatal shooting in August",       # a month
    "Teen arrested after a shooting in the Lakewood Crest area",   # more of the name before it
    "Gas leak closes a New Hampshire school",
])
def test_words_that_only_look_like_towns_are_not_placed(text) -> None:
    assert place(text) in (None, "Lebanon", "New Hampshire")      # a country or a state, not a town


def test_a_state_the_text_names_picks_the_town() -> None:
    assert place("Water main break in Springfield, Illinois officials say") == "Springfield, IL"


def test_titles_name_the_place() -> None:
    assert titled_with_place("Shooting", "East Providence, RI") == "Shooting — East Providence, RI"
    assert titled_with_place("Shooting · 1 injured", "Bronx, NY") == "Shooting — Bronx, NY · 1 injured"
    assert titled_with_place("Shooting — Texas", "Brownsville, TX") == "Shooting — Texas"        # already says where
    assert titled_with_place("Oldsmar Walmart evacuated", "Oldsmar, FL") == "Oldsmar Walmart evacuated"
    assert clean_title("Shooting", Place(state="WV")) == "Shooting — West Virginia"
    assert clean_title("TULSA OK - RIVER FLOODING", Place(state="OK")) == "TULSA OK - RIVER FLOODING"


def test_the_geocoder_is_asked_once_per_place_and_spaced_out(monkeypatch) -> None:
    import urllib.request

    from s2_watch import geocoding

    asked, slept, cache = [], [], {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps([{"lat": "40.5", "lon": "-80.0", "class": "place", "type": "village"}]).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: asked.append(request.full_url) or Reply())
    monkeypatch.setattr(geocoding.time, "sleep", slept.append)
    geocoder = geocoding.NominatimGeocoder(lambda key: cache.get(key, False), cache.__setitem__)
    assert geocoder.geocode("Etna, USA") == (40.5, -80.0)          # not cached yet: asked (it never was before 0.53.0)
    assert geocoder.geocode("Etna, USA") == (40.5, -80.0) and len(asked) == 1
    geocoder.geocode("Sisters, Oregon, USA")
    assert len(asked) == 2 and slept and slept[-1] > 1.0           # at most one request a second


def test_only_names_are_sent_to_the_geocoder() -> None:
    assert written_as_name("ETNA", "Crews repair water main break in Etna")
    assert not written_as_name("SHOOTING", "Teen hurt in shooting in a parking lot")
    assert written_as_name("SHOOTING", "TEEN HURT IN SHOOTING")            # radio: all capitals
    assert written_as_name("NEW RICHLAND", "Flooding in New Richland")


def test_events_saved_without_a_place_get_one(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    from s2_watch.analysis.base import AnalysisResult
    from s2_watch.events import Place as EventPlace
    from s2_watch.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "s2.db")
    message = Message("OSINT", "1 injured in shooting in East Providence", fields={"type": "NEWS"})
    message_id = store.save_message(message, "k")
    result = AnalysisResult(True, "Crime", "medium", "Shooting · 1 injured", 0.9, ("shooting",))
    store.save_analysis(message_id, result)
    report = store.assign_report(message_id, message, result)
    event_id = store.assign_event(report.report_id, "Crime", set(), EventPlace(), "Shooting · 1 injured")
    [(found_event, found_report, saved)] = store.unplaced_events(datetime.now(timezone.utc) - timedelta(days=1))
    assert (found_event, found_report) == (event_id, report.report_id)
    [location] = LocationResolver().resolve(saved)
    store.place_event(event_id, report.report_id, location)
    title, state, region = store.connection.execute(
        "SELECT title, state, region FROM events WHERE id = ?", (event_id,)).fetchone()
    assert (title, state, region) == ("Shooting — East Providence, RI · 1 injured", "RI", 1)
    summary = store.connection.execute("SELECT summary FROM analysis_results WHERE message_id = ?", (message_id,)).fetchone()
    assert summary == ("Shooting — East Providence, RI · 1 injured",)
    assert store.unplaced_events(datetime.now(timezone.utc) - timedelta(days=1)) == []
