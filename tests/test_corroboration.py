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


def test_osint_items_confirm_matching_reports() -> None:
    from datetime import timedelta

    from s2_watch.corroboration import official_items

    quake = Message("OSINT", "USGS: M4.1 earthquake — 10 km N of Ridgecrest, CA (35.700, -117.670)",
                    received_at=RECEIVED + timedelta(minutes=10),
                    fields={"type": "EARTHQUAKE", "magnitude": "4.1", "lat": "35.7", "lon": "-117.67"})
    hail = Message("OSINT", "SPC storm report: Hail 1.75 in — 3 N Norman, Cleveland County, OK (35.260, -97.440). Report.",
                   received_at=RECEIVED - timedelta(hours=2),
                   fields={"type": "SPC STORM REPORT", "lat": "35.26", "lon": "-97.44", "state": "OK"})
    fema = Message("OSINT", "FEMA Emergency declaration EM-3654: Flooding — Iowa; incident type Flood; 4 areas designated",
                   received_at=RECEIVED - timedelta(days=3), fields={"type": "FEMA DECLARATION", "state": "IA"})
    items = official_items([quake, hail, fema])

    text = "FELT A STRONG EARTHQUAKE IN BAKERSFIELD, CA"   # ~125 km from the epicentre
    found = corroborate(text, locations(text), RECEIVED, [], items)
    assert found.text() == "✓ USGS M4.1 quake · radio 10 min earlier"

    text = "LARGE HAIL IN NORMAN, OK"
    assert corroborate(text, locations(text), RECEIVED, [], items).text() == "✓ SPC hail report"

    text = "IA - FLOODING ON RIVER ROADS"   # state-level report: only FEMA can confirm it
    assert corroborate(text, locations(text), RECEIVED, [], items).text() == "✓ FEMA flood declaration"

    text = "FELT AN EARTHQUAKE IN DENVER, CO"   # too far from the quake
    assert corroborate(text, locations(text), RECEIVED, [], items) is None


def test_current_alert_does_not_confirm_an_old_report() -> None:
    from datetime import timedelta

    text = "FLASH FLOODING IN TULSA, OK"
    week_old = RECEIVED - timedelta(days=7)
    alert = dict(POLYGON_ALERT, onset="2026-09-27T18:25:00+00:00", ends="2026-09-28T06:00:00+00:00")
    assert corroborate(text, locations(text), week_old, [alert]) is None
    assert corroborate(text, locations(text), RECEIVED, [alert]).lead_minutes == 25


def test_small_quakes_confirm_reports_but_do_not_become_reports(monkeypatch) -> None:
    from s2_watch.adapters import osint

    small = Message("OSINT", "USGS: M2.7 earthquake", fields={"type": "EARTHQUAKE", "notable": "", "message_id": "q1"})
    large = Message("OSINT", "USGS: M4.6 earthquake", fields={"type": "EARTHQUAKE", "notable": "1", "message_id": "q2"})
    monkeypatch.setattr(osint, "_CACHE", {})
    adapter = osint.OsintAdapter(None, fetchers={"usgs": lambda: [small, large]})
    try:
        import time
        for _ in range(50):
            delivered = adapter.read_messages()
            if delivered:
                break
            time.sleep(0.02)
        assert [m.fields["message_id"] for m in delivered] == ["q2"]
        assert {m.fields["message_id"] for m in osint.cached_messages({"EARTHQUAKE"})} == {"q1", "q2"}
    finally:
        adapter.close()
