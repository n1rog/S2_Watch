from datetime import datetime, timezone

from s2_watch.corroboration import corroborate, hazards
from s2_watch.locating import LocationResolver
from s2_watch.models import Message

RECEIVED = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
POLYGON_ALERT = {
    "event": "Flash Flood Warning", "severity": "Severe", "sent": "2026-09-27T18:25:00+00:00",
    "geometry": {"type": "Polygon", "coordinates": [[[-96.2, 35.9], [-95.7, 35.9], [-95.7, 36.4], [-96.2, 36.4], [-96.2, 35.9]]]},
    "counties": [],
}
COUNTY_ALERT = {  # Heat Advisory for Maricopa County, AZ (FIPS 04013), issued before the report
    "event": "Heat Advisory", "severity": "Moderate", "sent": "2026-09-27T15:00:00+00:00",
    "geometry": None, "counties": ["04013"],
}


def locations(text: str, **fields):
    return LocationResolver().resolve(Message("MagCommstat", text, fields=fields))


def test_polygon_alert_confirms_matching_hazard_and_notes_radio_lead() -> None:
    found = corroborate("FLASH FLOODING IN TULSA, OK", locations("FLASH FLOODING IN TULSA, OK"), RECEIVED, [POLYGON_ALERT])
    assert found is not None and found.event == "Flash Flood Warning"
    assert found.lead_minutes == 25 and found.text() == "✓ NWS Flash Flood Warning · radio 25 min earlier"


def test_county_coded_alert_confirms_and_no_lead_when_alert_came_first() -> None:
    text = "HEAT INDEX 115 IN PHOENIX, AZ"
    found = corroborate(text, locations(text), RECEIVED, [COUNTY_ALERT])
    assert found is not None and found.text() == "✓ NWS Heat Advisory"


def test_no_match_for_other_hazards_places_or_coarse_locations() -> None:
    # Right place, different hazard.
    assert corroborate("POWER OUTAGE IN TULSA, OK", locations("POWER OUTAGE IN TULSA, OK"), RECEIVED, [POLYGON_ALERT]) is None
    # Right hazard, different place.
    assert corroborate("FLOODING IN DENVER, CO", locations("FLOODING IN DENVER, CO"), RECEIVED, [POLYGON_ALERT]) is None
    # A state-level location is too coarse to confirm anything.
    assert corroborate("MO - FLOOD WATCH", locations("MO - FLOOD WATCH"), RECEIVED, [POLYGON_ALERT]) is None


def test_hazard_families() -> None:
    assert hazards("EXCESSIVE HEAT WARNING") == {"heat"}
    assert hazards("Winter Storm Warning") == {"winter"}
    assert "thunderstorm" in hazards("SEVERE TSTORMS WITH HAIL")
