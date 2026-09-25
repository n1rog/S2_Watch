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
