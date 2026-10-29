import json
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
    monkeypatch.setattr(threading, "Thread", lambda target, daemon, **_: type("T", (), {"start": staticmethod(target)})())
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


def test_each_request_falls_back_on_its_own_and_adsb_fi_format_is_read(monkeypatch) -> None:
    import urllib.error

    import s2_watch.overlays as overlays

    monkeypatch.setattr(overlays, "REQUEST_SPACING_S", 0)

    def fake(url, timeout=15):
        if "adsb.lol" in url and "/39.0/" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        if "adsb.lol" in url:
            return {"ac": [{"hex": "a1", "lat": 40.0, "lon": -100.0}]}
        return {"aircraft": [{"hex": "f1", "lat": 39.0, "lon": -100.0}]}   # adsb.fi's key

    monkeypatch.setattr(overlays, "_get_json", fake)
    records, source = overlays._fetch_part("national", [(40.0, -100.0, 250), (39.0, -100.0, 250)])
    assert [record["hex"] for record in records] == ["a1", "f1"]
    assert source == "adsb.lol + adsb.fi"


def test_air_ambulances_flying_under_tail_numbers_are_medevac() -> None:
    import json
    from pathlib import Path

    from s2_watch.overlays import compact_aircraft, is_air_medical_operator

    registered = json.loads((Path(__file__).parent.parent / "s2_watch" / "data" / "air_ambulance.json").read_text(encoding="utf-8"))
    hex_code, operator = next(iter(registered.items()))
    base = {"lat": 39.7, "lon": -104.9, "dbFlags": 0, "t": "EC35", "category": "A7"}
    tail_number = compact_aircraft(dict(base, hex=hex_code, flight="N203HA"))
    assert tail_number["medical"] and tail_number["operator"] == operator
    # The feed's own owner field (adsb.fi) also counts.
    assert compact_aircraft(dict(base, hex="zzzzzz", flight="N1", ownOp="PHI HEALTH LLC"))["medical"]
    for name in ("AIR METHODS LLC", "MEMORIAL HERMANN HEALTH SYSTEM", "CAREFLITE", "BOSTON MEDFLIGHT"):
        assert is_air_medical_operator(name), name
    for name in ("LOS ANGELES INTERNATIONAL AIRPORT", "ANGEL AVIATION INC", "COLUMBINE ANIMAL HOSPITAL INC",
                 "CORPORATE HOSPITALITY AVIATION LLC", "PORTLAND METRO AVIATION & FLYING CLUB"):
        assert not is_air_medical_operator(name), name


def test_hiding_civilian_and_medevac_skips_the_traffic_sweeps(monkeypatch) -> None:
    import threading
    from PySide6.QtCore import QCoreApplication

    import s2_watch.overlays as overlays

    QCoreApplication.instance() or QCoreApplication([])
    calls = []
    monkeypatch.setattr(overlays, "_fetch_part", lambda part, view: (calls.append(part), ([], "test"))[1])
    monkeypatch.setattr(threading, "Thread", lambda target, daemon, **_: type("T", (), {"start": staticmethod(target)})())
    monkeypatch.setattr(overlays, "focus_tiles", lambda regions: [(40.0, -100.0, 250)])
    poller = overlays.AircraftPoller()
    poller.set_general_traffic(False)
    poller.fetch()
    assert calls == ["military", "squawks"]
    calls.clear()
    poller.set_general_traffic(True)   # starts fetching the sweeps (timer not running here)
    poller.fetch()
    assert calls == ["national"]


def test_country_of_registration_from_the_icao_address() -> None:
    from s2_watch.icao_countries import registration_country
    from s2_watch.overlays import compact_aircraft

    assert registration_country("a1b2c3") == "United States"
    assert registration_country("AE4D4D") == "United States"      # US military block
    assert registration_country("c03a5f") == "Canada"
    assert registration_country("3c6589") == "Germany"
    assert registration_country("43c6f0") == "United Kingdom"
    assert registration_country("0d0abc") == "Mexico"
    assert registration_country("~2a4f10") == "" and registration_country("") == ""
    assert registration_country("f00001") == ""                    # not a state block
    plane = compact_aircraft({"hex": "c03a5f", "lat": 49.2, "lon": -123.1})
    assert plane["country"] == "Canada"


def test_the_united_states_and_world_buttons_move_the_map(tmp_path, main_window) -> None:
    import time

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    window = main_window(tmp_path / "view.db")
    window.resize(1200, 900)
    window.show()
    view = window.map_view
    deadline = time.monotonic() + 30
    while not view._page_ready and time.monotonic() < deadline:
        app.processEvents()
    if not view._page_ready:
        pytest.skip("the map page didn't load")

    def bounds():
        settle = time.monotonic() + 1.5   # the map animates to its new view
        while time.monotonic() < settle:
            app.processEvents()
        result = []
        view._web_view.page().runJavaScript(
            "map.invalidateSize(); JSON.stringify([map.getCenter().lat, map.getCenter().lng, map.getZoom()])",
            0, result.append)
        deadline = time.monotonic() + 5
        while not result and time.monotonic() < deadline:
            app.processEvents()
        return json.loads(result[0])

    window.map_world_button.click()
    lat, lon, world_zoom = bounds()
    assert world_zoom <= 2 and abs(lon) < 40
    window.map_us_button.click()
    lat, lon, us_zoom = bounds()
    assert us_zoom > world_zoom and 30 < lat < 45 and -105 < lon < -90
