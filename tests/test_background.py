"""The BACKGROUND section: what S2 Watch can state about an event or a period."""

from datetime import datetime, timedelta, timezone

from s2_watch.background import EVENT_PROMPT, PERIOD_PROMPT, event_background, period_background
from s2_watch.events import EventSummary
from s2_watch.models import Message
from s2_watch.s2_report import briefing_report
from s2_watch.storage import SQLiteStore

from tests.test_events import T0, TULSA, add

NOW = T0 + timedelta(days=3)
FLOOD_WARNING = {"event": "Flood Warning", "severity": "Severe", "ends": (NOW + timedelta(hours=6)).isoformat(),
                 "geometry": {"type": "Polygon", "coordinates": [[[-96.5, 35.8], [-95.5, 35.8], [-95.5, 36.5],
                                                                  [-96.5, 36.5], [-96.5, 35.8]]]}, "counties": []}
SMALL_CRAFT = {"event": "Small Craft Advisory", "severity": "Minor", "ends": "", "geometry": None, "counties": []}


def summary(title, first, region=6, state="OK", points=((36.15, -95.99),), closed=None):
    return EventSummary(1, title, "Climate / Environment", state, region, first, first, 2, ("MagCommstat",), "high",
                        "", None, points=list(points), closed_at=closed)


def test_a_follow_up_states_alerts_earlier_events_and_declarations(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    earlier = add(store, "MagCommstat", "TULSA OK - RIVER FLOODING, ROADS CLOSED", T0, TULSA)
    store.close_event(earlier, "water receded", when=T0 + timedelta(hours=8))
    now_event = add(store, "MagCommstat", "TULSA OK - FLOODING AGAIN ON RIVERSIDE DR", T0 + timedelta(days=3), TULSA)
    fema = Message("OSINT", "FEMA: Major Disaster — Oklahoma; incident type Flood; 12 areas designated",
                   received_at=NOW - timedelta(days=2), fields={"type": "FEMA DECLARATION", "state": "OK"})
    [event] = [e for e in store.events_since(NOW - timedelta(days=1)) if e.event_id == now_event]
    wind = {**FLOOD_WARNING, "event": "Wind Advisory", "severity": "Moderate"}          # a different hazard
    lines = event_background(event, store.related_events(now_event), [FLOOD_WARNING, wind], [fema], NOW,
                             store.event_hazards(now_event))
    assert lines[0].startswith("NWS watches and warnings for this hazard in effect at the location: Flood Warning (until ")
    assert "Wind Advisory" not in lines[0]
    assert "01 Oct: TULSA OK - RIVER FLOODING, ROADS CLOSED (1 report, closed: water receded)" in lines[1]
    assert lines[2].startswith("FEMA declarations for Oklahoma in the last 30 days: Major Disaster — Oklahoma")
    assert lines[-1] == EVENT_PROMPT


def test_a_quiet_place_says_so() -> None:
    lines = event_background(summary("Hail", NOW), [], [], [], NOW, {"thunderstorm"})
    assert lines[:2] == ["No NWS watches or warnings for this hazard are in effect at the location as of this DTG.",
                         "S2 Watch recorded no similar events within 150 km or in the same state in the previous 30 days."]


def test_a_report_that_isnt_about_a_hazard_mentions_no_weather_or_fema() -> None:
    fema = Message("OSINT", "FEMA: Major Disaster — Oklahoma; incident type Flood; 12 areas designated",
                   received_at=NOW - timedelta(days=2), fields={"type": "FEMA DECLARATION", "state": "OK"})
    crime = event_background(summary("Shooting — Tulsa, OK", NOW), [], [FLOOD_WARNING], [fema], NOW, set())
    assert not any("NWS" in line or "FEMA" in line for line in crime)
    fire = event_background(summary("Grass fire — Tulsa, OK", NOW), [], [FLOOD_WARNING], [fema], NOW, {"fire"})
    assert not any("FEMA" in line for line in fire) and "Flood Warning" not in " ".join(fire)


def test_a_briefing_states_conditions_ongoing_situations_and_magcon() -> None:
    ongoing = summary("Grasshopper Fire — Oregon", NOW - timedelta(days=20), state="OR")
    new = summary("Flooding — Tulsa, OK", NOW - timedelta(hours=2))
    lines = period_background([ongoing, new], [FLOOD_WARNING, SMALL_CRAFT], [], NOW, NOW - timedelta(hours=24),
                              "3 – ELEVATED (NO CHANGE)")
    assert lines[0] == ("Standing conditions: 1 NWS watches and warnings (marine ones aside) are active across the U.S.: "
                        "1 severe (Flood Warning).")
    assert lines[1].startswith("Ongoing situations that began before this period: Grasshopper Fire — Oregon (since ")
    assert "MagCon status: 3 – ELEVATED (NO CHANGE)." in lines and lines[-1] == PERIOD_PROMPT
    text = briefing_report([ongoing, new], NOW, 24, background=lines)
    section = text.split("BACKGROUND", 1)[1].split("SITUATION", 1)[0]
    assert "Standing conditions" in section and "(Factual context for the period" not in section
