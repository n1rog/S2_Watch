"""BLUFs say where: an alert's own area, a town or county for a bare point, then the state."""

from datetime import datetime, timezone

from s2_watch.adapters.osint_more import ipaws_area
from s2_watch.analysis.base import AnalysisResult
from s2_watch.events import EventSummary
from s2_watch.followup import FollowUpFacts
from s2_watch.locating import LocationResolver, ResolvedLocation, describe_point
from s2_watch.models import Message
from s2_watch.s2_report import briefing_report, event_where, followup_report, where

SEDONA_AREA = [{"areaDesc": "Sedona 8-9-10", "geocode": [{"name": "SAME", "value": "004005"}],
                "polygon": {"type": "Polygon", "coordinates": [[[-111.7517, 34.8748], [-111.7558, 34.8772],
                                                                 [-111.7633, 34.8669], [-111.7838, 34.8400],
                                                                 [-111.7517, 34.8748]]]}}]
T = datetime(2026, 11, 1, 15, 2, tzinfo=timezone.utc)


def sedona_alert() -> Message:
    return Message("OSINT", "Public alert (IPAWS): Evacuation Immediate — Sedona 8-9-10", received_at=T,
                   fields={"type": "IPAWS ALERT", "sender": "AZCoconinoCountyDepartmentofEmergencyManagement",
                           "official": "1", **ipaws_area(SEDONA_AREA)})


def test_an_ipaws_alert_carries_its_outline_county_and_area_name() -> None:
    assert ipaws_area(SEDONA_AREA) == {"lat": "34.8667", "lon": "-111.7613", "county": "Coconino County",
                                       "state": "AZ", "area": "Sedona 8-9-10"}
    text_polygon = [{"areaDesc": "Area", "polygon": "36.10,-96.00 36.20,-96.00 36.20,-95.90"}]
    assert ipaws_area(text_polygon) == {"lat": "36.1667", "lon": "-95.9667"}   # "Area" says nothing


def test_an_alert_is_placed_at_its_own_area_not_the_middle_of_the_state() -> None:
    [place] = LocationResolver().resolve(sedona_alert())
    assert (place.label, place.level, place.state) == ("Sedona 8-9-10, Coconino County, AZ", "precise", "AZ")
    assert (round(place.lat, 2), round(place.lon, 2)) == (34.87, -111.76)


def test_a_point_is_named_by_a_nearby_town_or_else_its_county() -> None:
    assert describe_point(36.15, -95.99) == "Tulsa, OK"
    assert describe_point(34.86, -111.77) == "Coconino County, AZ"
    assert where(36.154, -95.993, "Tulsa, OK") == "Tulsa, OK (36.154, -95.993)"
    assert where(33.396, -111.542, "Grid DM43FJ") == "Pinal County, AZ (grid DM43FJ) (33.396, -111.542)"
    assert where(state="AZ") == "Arizona"


def test_a_follow_up_bluf_names_the_place_as_exactly_as_it_is_known() -> None:
    message = sedona_alert()
    facts = FollowUpFacts(message, AnalysisResult(True, "Climate / Environment", "high",
                                                  "Public alert (IPAWS): Evacuation Immediate — Sedona 8-9-10", 0.9),
                          LocationResolver().resolve(message), state="AZ", official="✓ FEMA IPAWS")
    text = followup_report(facts, now=T)
    bluf = text.split("SUMMARY (BLUF)\n", 1)[1].split("\n", 1)[0]
    assert "Evacuation Immediate in Sedona 8-9-10, Coconino County, AZ (34.867, -111.761)." in bluf


def test_an_alert_saved_without_its_outline_is_placed_by_its_text_and_sending_county() -> None:
    old = Message("OSINT", "Public alert (IPAWS): Evacuation Immediate — Sedona 8-9-10", received_at=T,
                  fields={"type": "IPAWS ALERT", "sender": "AZCoconinoCountyDepartmentofEmergencyManagement",
                          "official": "1"})
    [place] = LocationResolver().resolve(old)
    assert (place.label, place.level, place.state) == ("Sedona 8-9-10, Coconino County, AZ", "county", "AZ")
    facts = FollowUpFacts(old, AnalysisResult(True, "Climate / Environment", "high", "Evacuation Immediate", 0.9),
                          [place], region=9, state="AZ")
    bluf = followup_report(facts, now=T).split("SUMMARY (BLUF)\n", 1)[1].split("\n", 1)[0]
    # A county's centre is far from Sedona: named, without coordinates.
    assert "Evacuation Immediate in Sedona 8-9-10, Coconino County, AZ, MARS Region 9." in bluf


def test_a_briefing_names_where_its_most_significant_event_is() -> None:
    event = EventSummary(1, "Evacuation Immediate", "Climate / Environment", "AZ", 9, T, T, 2, ("MagCommstat",),
                         "high", "FEMA IPAWS", None, points=[(34.417, -111.591), (34.8667, -111.7613)],
                         placed=[(34.417, -111.591, "state"), (34.8667, -111.7613, "precise")])
    assert event_where(event) == "Coconino County, AZ (34.867, -111.761)"
    state_only = EventSummary(2, "Evacuation Immediate", "Climate / Environment", "AZ", 9, T, T, 1, ("OSINT",),
                              "high", "", None, points=[(34.417, -111.591)], placed=[(34.417, -111.591, "state")])
    assert event_where(state_only) == "Arizona"     # the state's centre isn't named as a county
    text = briefing_report([event], hours=24, now=T)
    assert "Most significant: Evacuation Immediate — Coconino County, AZ (34.867, -111.761)." in text
