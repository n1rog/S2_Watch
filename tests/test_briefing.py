from datetime import datetime, timedelta, timezone

from s2_watch.briefing import briefing_report, js8_briefing
from s2_watch.events import EventSummary

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def event(event_id, title, urgency, region, state, sources=("MagCommstat",), official="", lead=None, reports=1,
          hours_ago=2, category="Climate / Environment", statuses=()):
    seen = NOW - timedelta(hours=hours_ago)
    return EventSummary(event_id, title, category, state, region, seen, seen, reports, tuple(sources), urgency,
                        official, lead, tuple(statuses))


EVENTS = [
    event(1, "Bouquet Fire — California · 1,000 acres", "high", 9, "CA", ("OSINT",), "NIFC"),
    event(2, "Flooding — Tulsa, OK", "high", 6, "OK", ("MagCommstat", "JS8Call", "OSINT"), "NWS Flood Warning", 45, 3),
    event(3, "Heat Advisory — California", "medium", 9, "CA", hours_ago=30),
    event(4, "Shooting — Omaha, NE", "high", 7, "NE", ("OSINT",), category="Crime", statuses=("ignored",)),
    event(5, "Net check", "low", 8, "CO"),
]


def test_briefing_puts_focus_and_radio_events_first_and_skips_ignored() -> None:
    text = briefing_report(EVENTS, NOW, 24, "N1ROG", focus={6}, last_briefing=NOW - timedelta(hours=12))
    lines = text.splitlines()
    assert "FROM: N1ROG" in lines and "FOCUS: MARS REGION 6" in lines
    assert "PERIOD: 081500Z OCT 26 TO 091500Z OCT 26 (24 HOURS)" in lines
    items = [line for line in lines if line[:2] in ("1.", "2.", "3.", "4.")]
    assert items[0].startswith("1. [HIGH] Flooding — Tulsa, OK — MARS R6. 3 reports (MagCommstat, JS8Call, OSINT); "
                               "Official: NWS Flood Warning; radio 45 min ahead. [NEW]")
    assert items[1].startswith("2. [HIGH] Bouquet Fire")
    assert not any("Omaha" in line for line in lines)           # ignored
    assert any(line.startswith("R6 (focus): 1 event") for line in lines)
    assert any(line.startswith("QUIET: ") and "Terrorism" in line for line in lines)
    assert "radio reported 1 before official sources" in text and "3 new since the last briefing" in text


def test_js8call_briefing_is_short_and_from_the_full_text() -> None:
    text = briefing_report(EVENTS, NOW, 24, "N1ROG", focus={6})
    js8 = js8_briefing(text.replace("Flooding — Tulsa, OK", "Flooding — Sand Springs, OK"))
    assert js8.startswith("S2 BRIEF 091500Z OCT 26 4 EVENTS 1) HIGH FLOODING SAND SPRINGS, OK 2) HIGH BOUQUET FIRE CALIFORNIA")
    assert js8.endswith("DE N1ROG") and len(js8) <= 200


def test_priority_items_need_a_us_place_unless_radio_reported_them() -> None:
    foreign = event(9, "Earthquake — -15.54, 168.19 · M6.3", "high", None, "", ("OSINT",), "USGS")
    unplaced_radio = event(10, "Bridge out", "high", None, "", ("JS8Call",))
    text = briefing_report(EVENTS + [foreign, unplaced_radio], NOW, 24)
    assert "Earthquake" not in text.split("BY MARS REGION")[0] and "Bridge out" in text
