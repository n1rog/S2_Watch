from datetime import datetime, timedelta, timezone

from s2_watch.models import Message
from s2_watch.overlays import CoverageTracker, compact_alerts, station_grid


def test_alerts_keep_outlines_or_county_codes_and_sort_worst_last() -> None:
    payload = {"features": [
        {"geometry": None, "properties": {"event": "Heat Advisory", "severity": "Moderate",
                                          "geocode": {"SAME": ["004013", "004021"]}}},
        {"geometry": {"type": "Polygon", "coordinates": [[[-97.123456, 35.1], [-97.2, 35.2], [-97.1, 35.3]]]},
         "properties": {"event": "Tornado Warning", "severity": "Extreme", "geocode": {"SAME": ["040109"]}}},
        {"geometry": None, "properties": {"event": "Nothing to draw", "severity": "Minor", "geocode": {}}},
    ]}
    alerts = compact_alerts(payload)
    assert [alert["event"] for alert in alerts] == ["Heat Advisory", "Tornado Warning"]
    assert alerts[0]["counties"] == ["04013", "04021"]
    assert alerts[1]["counties"] == [] and alerts[1]["geometry"]["coordinates"][0][0] == [-97.123, 35.1]


def test_station_grid_from_fields_or_status_line() -> None:
    assert station_grid(Message("X", "ALL GREEN", fields={"grid": "dm79qc"})) == "DM79"
    assert station_grid(Message("X", "W1NEM: @MAGNET  ,EM29RL,3,Y48,211111111111,MO - FLOOD WATCH")) == "EM29"
    assert station_grid(Message("X", "NET CHECK @MR06OK")) is None


def test_coverage_keeps_latest_time_and_counts_each_message_once() -> None:
    tracker = CoverageTracker()
    old = datetime(2026, 9, 24, 10, tzinfo=timezone.utc)
    first = Message("JS8Call", "W1AW: ,FN31PR,1,A1,111,HI", received_at=old, fields={"sender": "W1AW"})
    tracker.add(first)
    tracker.add(first)  # read again by a re-scan
    tracker.add(Message("JS8Call", "K1ABC: ,FN31AA,1,A2,111,HELLO", received_at=old + timedelta(hours=3)))
    [cell] = tracker.snapshot()
    assert cell["grid"] == "FN31" and cell["messages"] == 2
    assert cell["last"] == "2026-09-24T13:00:00+00:00"
    assert cell["south"] == 41 and cell["west"] == -74
    assert set(cell["stations"]) == {"W1AW", "K1ABC"}


def test_aircraft_records_are_compacted_and_merged_by_icao_address() -> None:
    from s2_watch.overlays import compact_aircraft, merge_aircraft

    military = {"hex": "ae4d4d", "flight": "LT611   ", "r": "168567", "t": "H60", "lat": 31.94787, "lon": -117.8163,
                "alt_baro": 200, "gs": 73.2, "track": 4.7, "squawk": "4000", "emergency": "none", "dbFlags": 1}
    emergency = {"hex": "a1b2c3", "flight": "SWA123", "lat": 35.0, "lon": -106.6, "alt_baro": "ground",
                 "squawk": "7700", "dbFlags": 0}
    assert compact_aircraft({"hex": "abc123"}) is None  # no position
    plane = compact_aircraft(military)
    assert plane["flight"] == "LT611" and plane["military"] and not plane["emergency"] and plane["speed"] == 73
    assert compact_aircraft(emergency)["emergency"] == "Emergency" and compact_aircraft(emergency)["altitude"] == "ground"
    # The same aircraft from two queries appears once, with the later record.
    moved = dict(military, lat=32.0)
    assert [a["lat"] for a in merge_aircraft([military, emergency, moved]) if a["hex"] == "ae4d4d"] == [32.0]


def test_aircraft_poller_backs_off_when_rate_limited(monkeypatch) -> None:
    import threading
    from PySide6.QtCore import QCoreApplication

    import s2_watch.overlays as overlays

    QCoreApplication.instance() or QCoreApplication([])
    calls = []

    def limited(part, view):
        calls.append(part)
        raise overlays.RateLimited("adsb.lol: HTTP 429")

    monkeypatch.setattr(overlays, "_fetch_part", limited)
    monkeypatch.setattr(threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)})())
    poller = overlays.AircraftPoller()
    messages = []
    poller.failed.connect(messages.append)
    poller.fetch()
    poller.fetch()  # still backing off: no second request
    assert calls == ["military"]
    assert "retrying in 1 min" in messages[0]


def test_aircraft_shapes_and_air_ambulance_callsigns() -> None:
    from s2_watch.overlays import compact_aircraft

    base = {"lat": 39.7, "lon": -104.9, "dbFlags": 0}
    helicopter = compact_aircraft(dict(base, hex="a1", flight="LIFELN3 ", t="AS50", category="A7"))
    assert helicopter["shape"] == "helicopter" and helicopter["medical"]
    cessna = compact_aircraft(dict(base, hex="a2", flight="N5178S", t="C172", category="A1"))
    assert cessna["shape"] == "light" and not cessna["medical"]
    airliner = compact_aircraft(dict(base, hex="a3", flight="UAL123", t="B738", category="A3"))
    assert airliner["shape"] == "plane" and not airliner["medical"]
    assert compact_aircraft(dict(base, hex="a4", flight="N480TH", t="EN48"))["shape"] == "helicopter"


def test_focus_tiles_cover_the_region() -> None:
    import math

    from s2_watch.overlays import MAX_RADIUS_NM, focus_tiles

    def covered(lat, lon, tiles):
        return any(math.hypot(lat - t_lat, (lon - t_lon) * math.cos(math.radians(lat))) * 60 <= r
                   for t_lat, t_lon, r in tiles)

    tiles = focus_tiles({8})
    assert 3 <= len(tiles) <= 10 and all(radius == MAX_RADIUS_NM for _lat, _lon, radius in tiles)
    for lat, lon in ((39.74, -104.99), (46.59, -112.04), (40.76, -111.89), (46.81, -100.78)):  # Denver, Helena, SLC, Bismarck
        assert covered(lat, lon, tiles), (lat, lon)
    assert not covered(33.45, -112.07, tiles) or not covered(29.76, -95.37, tiles)  # not Phoenix and Houston
    assert focus_tiles(set()) == []
