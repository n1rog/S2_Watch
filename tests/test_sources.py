"""The sources added in 0.29, read from small samples of each feed (no network)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from s2_watch.adapters import osint, osint_more
from s2_watch.adapters.aprs_is import AprsIsAdapter, parse_bulletin
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.events import incident_key

NOW = datetime.now(timezone.utc)
RULES = RuleBasedNewsAnalyzer()


def serve(monkeypatch, body) -> None:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    monkeypatch.setattr(osint_more, "_get", lambda url: data)


def test_public_alerts_keep_local_authorities_and_drop_tests_and_the_weather_service(monkeypatch) -> None:
    def alert(event, sender, headline, msg_type="Alert", area="Thornton, CO"):
        return {"identifier": f"id-{event}", "sent": (NOW - timedelta(hours=3)).isoformat(), "status": "Actual",
                "msgType": msg_type, "info": [{"event": event, "senderName": sender, "headline": headline,
                                               "area": [{"areaDesc": area}, {"areaDesc": "AffectedArea"}]}]}
    serve(monkeypatch, {"IpawsArchivedAlerts": [
        alert("Shelter in Place Warning", "200171,Thornton", "Shelter in place 120th to 128th"),
        alert("Required Weekly Test", "IPAWS", "RWT"),
        alert("Flash Flood Warning", "NWS Salt Lake City UT", "Flash flood"),
        alert("Civil Emergency Message", "Emery County Sheriffs Office UT", "Road closed", msg_type="Cancel"),
    ]})
    [item] = osint_more.fetch_ipaws()
    assert item.fields["sender"] == "Thornton" and item.fields["official"] == "1"
    assert item.raw_content == "Public alert (IPAWS): Shelter in Place Warning — Shelter in place 120th to 128th — Thornton, CO"
    assert RULES.analyze(item).category == "Climate / Environment"


def test_space_weather_keeps_storms_that_matter_for_hf(monkeypatch) -> None:
    def product(message):
        return {"product_id": "K07A", "issue_datetime": "2026-10-10 19:27:00.000", "message": message}
    serve(monkeypatch, [product("ALERT: Geomagnetic K-index of 7\nNOAA Scale: G3 - Strong"),
                        product("ALERT: Geomagnetic K-index of 5\nNOAA Scale: G1 - Minor")])
    [item] = osint_more.fetch_space_weather()
    assert "G3 geomagnetic storm; HF radio propagation affected" in item.raw_content
    assert incident_key(item) == "space:G" and RULES.analyze(item).category == "Infrastructure"


def test_storm_reports_skip_what_spc_covers_and_small_amounts(monkeypatch) -> None:
    def report(kind, magnitude, source="Trained Spotter"):
        return {"properties": {"typetext": kind, "magnitude": magnitude, "unit": "Inch", "city": "5 E Tulsa",
                               "county": "Tulsa", "state": "OK", "lat": 36.15, "lon": -95.9, "valid": "2026-10-20T12:00:00Z",
                               "source": source, "wfo": "TSA", "remark": None, "product_id": f"p-{kind}-{magnitude}"}}
    serve(monkeypatch, {"features": [report("FLASH FLOOD", None, "Amateur Radio"), report("HAIL", 1.75),
                                     report("HEAVY SNOW", 2), report("HEAVY SNOW", 8)]})
    flood, snow = osint_more.fetch_storm_reports()
    assert flood.raw_content.startswith("NWS storm report: Flash flooding — 5 E Tulsa, Tulsa County, OK (36.150, -95.900).")
    assert "Reported by amateur radio." in flood.raw_content and flood.fields["lat"] == "36.15"
    assert "Heavy snowfall 8 inch" in snow.raw_content


def test_rivers_storms_volcanoes_and_outages_are_one_event_each(monkeypatch) -> None:
    serve(monkeypatch, {"features": [{"attributes": {
        "gaugelid": "ASTF1", "status": "moderate", "location": "Astor", "waterbody": "St. Johns River", "state": "FL",
        "obstime": "2026-10-20 16:30:00", "observed": "3.1", "units": "ft", "flood": "2.3", "latitude": 29.17,
        "longitude": -81.52, "url": "https://water.noaa.gov/gauges/astf1"}}]})
    [river] = osint_more.fetch_river_flooding()
    assert "MODERATE flood stage" in river.raw_content and incident_key(river) == "gauge:ASTF1"
    serve(monkeypatch, {"activeStorms": [{"id": "al052026", "name": "Ernesto", "classification": "HU", "intensity": "85",
                                          "pressure": "975", "latitudeNumeric": 25.1, "longitudeNumeric": -80.2,
                                          "movementDir": 315, "movementSpeed": 12, "lastUpdate": "2026-10-20T15:00:00Z"}]})
    [storm] = osint_more.fetch_hurricanes()
    assert storm.raw_content.startswith("National Hurricane Center: Hurricane Ernesto — 85 kt winds")
    assert incident_key(storm) == "storm:al052026" and RULES.analyze(storm).urgency == "high"
    serve(monkeypatch, {"data": [
        {"entity": {"code": "4447", "name": "Virginia"}, "time": int(NOW.timestamp()) - 3600, "level": "critical",
         "value": 5269, "historyValue": 92748, "datasource": "ping-slash24"},
        {"entity": {"code": "4447", "name": "Virginia"}, "time": int(NOW.timestamp()) - 7200, "level": "critical",
         "value": 9000, "historyValue": 92748, "datasource": "ping-slash24"},
        {"entity": {"code": "4400", "name": "Ohio"}, "time": int(NOW.timestamp()), "level": "warning",
         "value": 1, "historyValue": 2, "datasource": "bgp"}]})
    [outage] = osint_more.fetch_internet_outages()          # the latest critical alert per state
    assert outage.raw_content == "Internet outage: Virginia — reachable networks at 6% of normal (IODA, critical)"
    assert incident_key(outage) == "outage:region:4447" and RULES.analyze(outage).category == "Infrastructure"


def test_airports_report_ground_stops_but_not_volume_or_scheduled_closures(monkeypatch) -> None:
    serve(monkeypatch, b"""<AIRPORT_STATUS_INFORMATION>
      <Delay_type><Name>Ground Stop Programs</Name><Ground_Stop_List>
        <Program><ARPT>LGA</ARPT><Reason>airport volume</Reason></Program>
        <Program><ARPT>ATL</ARPT><Reason>equipment / outage</Reason><End_Time>3:00 pm EDT</End_Time></Program>
      </Ground_Stop_List></Delay_type>
      <Delay_type><Name>Airport Closures</Name><Airport_Closure_List>
        <Airport><ARPT>SNA</ARPT><Reason>!SNA 10/025 SNA AD AP CLSD DLY 0630-1315</Reason></Airport>
        <Airport><ARPT>MSY</ARPT><Reason>hurricane</Reason><Reopen>Oct 21</Reopen></Airport>
      </Airport_Closure_List></Delay_type></AIRPORT_STATUS_INFORMATION>""")
    texts = [item.raw_content for item in osint_more.fetch_airport_status()]
    assert texts == ["FAA: ground stop at ATL airport — equipment / outage, until 3:00 pm EDT",
                     "FAA: MSY airport closed — hurricane, reopening Oct 21"]


def test_firms_needs_a_key_and_reports_clusters_not_single_hotspots(monkeypatch) -> None:
    assert osint_more.fetch_fire_detections() == []          # no key: nothing fetched
    day, hhmm = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d"), (NOW - timedelta(hours=1)).strftime("%H%M")
    old_day = (NOW - timedelta(hours=30)).strftime("%Y-%m-%d")
    header = "latitude,longitude,acq_date,acq_time,confidence"
    rows = [header] + [f"36.0{i},-96.1{i},{day},{hhmm},n" for i in range(8)]   # a fire: eight detections
    rows += [f"40.5,-100.5,{day},{hhmm},n"]                                    # a lone hotspot
    rows += [f"35.0{i},-97.0{i},{old_day},{hhmm},n" for i in range(8)]          # yesterday's: too old
    responses = iter(["\n".join(rows).encode()])                               # the lower 48; nothing elsewhere
    monkeypatch.setattr(osint_more, "_get", lambda url: next(responses, header.encode()))
    try:
        osint_more.set_firms_key("KEY")
        [cluster] = osint_more.fetch_fire_detections()
    finally:
        osint_more.set_firms_key("")
    assert cluster.raw_content.startswith("Satellite heat detection: 8 hotspots")
    result = RULES.analyze(cluster)
    assert result.newsworthy and result.urgency == "low"


def test_new_sources_are_registered_and_some_start_off() -> None:
    assert {"ipaws", "space", "nhc", "ioda", "lsr", "rivers", "airports", "volcanoes"} <= set(osint.FETCHERS)
    assert osint.DEFAULT_OFF == {"gdacs", "kev", "cbp", "firms"}
    assert set(osint.SOURCE_LABELS) == set(osint.FETCHERS) and set(osint.FETCHERS) <= set(osint.SCHEDULE)


def test_aprs_bulletins_from_us_stations_only_and_a_call_sign_is_needed() -> None:
    message = parse_bulletin("W5ABC-9>APRS,TCPIP*,qAC,T2TEXAS::BLN2WX   :Flash flood warning Tulsa county{AB1", NOW)
    assert message.raw_content == "W5ABC-9: BLN2WX: Flash flood warning Tulsa county"
    assert message.fields["sender"] == "W5ABC" and message.source_application == "APRS"
    assert parse_bulletin("F4AMY>APRS,qAS,F4AMY-13::BLN1     :Thunderstorm risk", NOW) is None   # not U.S.
    assert parse_bulletin("W5ABC>APRS::W1AW     :hello", NOW) is None                              # not a bulletin
    adapter = AprsIsAdapter(login="")
    assert adapter.status.startswith("Needs your call sign") and adapter.read_messages() == []


@pytest.mark.parametrize("text, category", [
    ("Public alert (IPAWS): Child Abduction Emergency — AMBER Alert — Texas, TX", "Crime"),
    ("Public alert (IPAWS): Local Area Emergency — IH20 West is closed at Spur 408", "Infrastructure"),
    ("USGS volcano: Great Sitkin ORANGE/WATCH — Slow eruption of lava (52.077, -176.111)", "Climate / Environment"),
    ("CISA known exploited vulnerability: CVE-2026-1 — Vendor Product: Bypass", "Infrastructure"),
])
def test_feed_wording_is_recognised(text, category) -> None:
    from s2_watch.models import Message

    result = RULES.analyze(Message("OSINT", text))
    assert result.newsworthy and result.category == category
