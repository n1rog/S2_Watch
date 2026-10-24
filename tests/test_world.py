"""Worldwide coverage: places abroad, world areas, and sources following the coverage."""

import json

import pytest

from s2_watch import world
from s2_watch.adapters import osint, osint_more
from s2_watch.adapters.aprs_is import parse_bulletin
from s2_watch.locating import LocationResolver
from s2_watch.models import Message

from tests.test_sources import NOW, serve


@pytest.fixture
def coverage():
    """Set the coverage for a test; back to the U.S. alone afterwards."""
    yield world.set_coverage
    world.set_coverage({"us"})


@pytest.mark.parametrize("text, place", [
    ("QST YEMEN SAUDI-LED COALITION AIRSTRIKES", ("country", "Yemen")),
    ("Drone attack on Kyiv overnight", ("city", "Kiev")),
    ("RIOTS IN LONDON", ("city", "London")),
    ("STRIKES IN LEBANON", ("country", "Lebanon")),
    ("STRIKES IN ATHENS, GREECE", ("city", "Athens")),
])
def test_places_abroad_are_found(text, place) -> None:
    assert world.load_world().find(text)[:2] == place


@pytest.mark.parametrize("text", [
    "LEBANON PA FLOODING",            # a U.S. town with its state
    "MOSCOW ID STORM",
    "PARIS TX TORNADO",
    "CHAD SAID THE NET IS OPEN",      # a name, not the country
    "TURKEY DINNER AT NET",
    "ATHENS POWER OUT",               # Athens, GA is big enough to be the likelier one
])
def test_us_places_and_ordinary_words_are_not_placed_abroad(text) -> None:
    assert world.load_world().find(text) is None


def test_us_places_come_first_and_points_get_their_area() -> None:
    resolver = LocationResolver()
    [tulsa] = resolver.resolve(Message("JS8Call", "W5ABC: FLOODING IN TULSA, OK"))
    assert tulsa.state == "OK" and world.location_area(tulsa) == "us"
    [paris] = resolver.resolve(Message("OSINT", "Protests in Paris, France turn violent"))
    assert paris.label == "Paris, France" and world.location_area(paris) == "europe"
    w = world.load_world()
    assert w.area_of(25.1, -80.2) == "us"                 # at sea off Miami: the nearest coast is American
    assert w.area_of(19.4, -99.1) == "north_america"      # Mexico City
    assert w.area_of(14.6, -90.5) == "central_america"    # Guatemala City
    assert w.area_of(18.2, -66.5) == "us"                 # Puerto Rico
    assert w.area_of(0.0, -30.0) is None                  # mid-Atlantic


def test_gdelt_asks_for_us_news_only_while_the_us_alone_is_covered(coverage) -> None:
    assert "sourcecountry%3AUS" in osint.gdelt_url()
    coverage({"us", "europe"})
    assert "sourcecountry" not in osint.gdelt_url()
    assert osint.covered_text("Riots in Paris, France") and not osint.covered_text("Riots in Lagos, Nigeria")


def test_quakes_follow_the_coverage_and_m6_counts_anywhere(monkeypatch, coverage) -> None:
    def quake(mag, lat, lon):
        return {"id": f"q{mag}{lat}", "properties": {"mag": mag, "time": int(NOW.timestamp() * 1000), "place": "x", "url": ""},
                "geometry": {"coordinates": [lon, lat, 10]}}
    feed = {"features": [quake(5.0, 35.7, 139.7), quake(6.4, -33.4, -70.6), quake(4.2, 36.1, -96.0)]}
    monkeypatch.setattr(osint, "_get", lambda url: json.dumps(feed).encode())
    us_only = osint.fetch_usgs_earthquakes()
    assert [m.fields["magnitude"] for m in us_only] == ["6.4", "4.2"]         # Japan M5.0 isn't covered
    coverage({"us", "asia"})
    assert [m.fields["magnitude"] for m in osint.fetch_usgs_earthquakes()] == ["5.0", "6.4", "4.2"]


def test_gdacs_and_country_outages_keep_covered_areas(monkeypatch, coverage) -> None:
    serve(monkeypatch, {"features": [
        {"properties": {"eventtype": "TC", "eventid": 1, "episodeid": 1, "name": "Cyclone A", "alertlevel": "Red",
                        "country": "Japan", "todate": NOW.isoformat()}, "geometry": {"coordinates": [139.7, 35.7]}},
        {"properties": {"eventtype": "FL", "eventid": 2, "episodeid": 1, "name": "Flood B", "alertlevel": "Orange",
                        "country": "Spain", "todate": NOW.isoformat()}, "geometry": {"coordinates": [-0.4, 39.5]}}]})
    coverage({"us", "europe"})
    assert [m.raw_content.split(":")[1].split("—")[0].strip() for m in osint_more.fetch_world_disasters()] == ["Flood B"]
    serve(monkeypatch, {"data": [
        {"entity": {"code": "UA", "name": "Ukraine"}, "time": int(NOW.timestamp()), "level": "critical",
         "value": 1, "historyValue": 10, "datasource": "bgp"},
        {"entity": {"code": "NG", "name": "Nigeria"}, "time": int(NOW.timestamp()), "level": "critical",
         "value": 1, "historyValue": 10, "datasource": "bgp"}]})
    coverage({"europe"})                                   # no U.S.: countries only
    assert [m.fields["state"] for m in osint_more.fetch_internet_outages()] == ["Ukraine"]


def test_us_sources_pause_without_the_us_and_aprs_opens_up(coverage) -> None:
    assert {"spc", "nifc", "fema"} <= osint.US_ONLY and "usgs" not in osint.US_ONLY
    line = "DL1ABC>APRS,TCPIP*::BLN1     :Hochwasser in Dresden"
    assert parse_bulletin(line, NOW, us_only=True) is None
    assert parse_bulletin(line, NOW, us_only=False).fields["sender"] == "DL1ABC"


def test_the_coverage_menu_sets_and_saves_the_coverage(tmp_path, main_window, coverage) -> None:
    window = main_window()
    assert set(window.coverage_actions) == {key for key, _label in world.AREAS}
    window.coverage_actions["europe"].setChecked(True)
    window.coverage_changed()
    assert world.COVERAGE == {"us", "europe"} and window.store.get_setting("coverage.areas") == "us,europe"
    assert "gdacs" in window.enabled_osint_sources()          # worldwide disasters switched on with it
    window.coverage_actions["us"].setChecked(False)
    window.coverage_actions["europe"].setChecked(False)
    window.coverage_changed()                                  # nothing ticked: back to the U.S.
    assert world.COVERAGE == {"us"} and window.coverage_actions["us"].isChecked()


def test_what_is_on_screen_hides_with_an_area_taken_out_of_the_coverage(main_window, coverage) -> None:
    from datetime import datetime, timezone

    from s2_watch.analysis.base import AnalysisResult
    from s2_watch.events import EventSummary

    resolver = LocationResolver()
    paris_message = Message("OSINT", "Protests in Paris, France turn violent")
    tulsa_message = Message("JS8Call", "W5ABC: FLOODING IN TULSA, OK")
    paris, tulsa = resolver.resolve(paris_message), resolver.resolve(tulsa_message)
    now = datetime.now(timezone.utc)

    def event(state, region, point):
        return EventSummary(1, "x", "Civil Unrest", state, region, now, now, 1, ("OSINT",), "high", "", None,
                            points=[point])
    coverage({"us", "europe"})
    assert world.locations_covered(paris) and world.event_covered(event("France", None, (48.86, 2.35)))
    window = main_window()
    window.rescan_sources = lambda: None          # what's already on screen, not a fresh re-read
    window.coverage_actions["europe"].setChecked(True)
    window.coverage_changed()
    result = AnalysisResult(True, "Civil Unrest", "high", "Protests", 0.8)
    window.map_view.add_message(paris_message, paris)
    window.map_view.add_message(tulsa_message, tulsa)
    window.insert_analysis_row(paris_message, result, None, paris)
    window.insert_analysis_row(tulsa_message, result, None, tulsa)
    window.apply_news_rows_limit()
    assert not any(window.analysis.isRowHidden(row) for row in range(2))
    window.coverage_actions["europe"].setChecked(False)
    window.coverage_changed()                                  # Europe out: Paris hides, Tulsa stays
    shown = [window.analysis.item(row, 0).data(window.LOCATIONS_ROLE)[0].label
             for row in range(window.analysis.rowCount()) if not window.analysis.isRowHidden(row)]
    assert shown == [tulsa[0].label]
    assert {marker["label"] for marker in window.map_view._visible_locations()} == {tulsa[0].label}
    assert not world.event_covered(event("France", None, (48.86, 2.35)))
    assert world.event_covered(event("OK", 6, (36.15, -95.99))) and world.locations_covered([])  # no place: shown
    advisory = Message("OSINT", "State Dept travel advisory: France - Level 2", fields={"type": "TRAVEL ADVISORY"})
    assert world.message_covered(advisory, paris) and not world.message_covered(paris_message, paris)
