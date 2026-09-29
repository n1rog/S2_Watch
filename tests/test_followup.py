from datetime import datetime, timezone

from s2_watch.analysis.base import AnalysisResult
from s2_watch.followup import FollowUpFacts, dtg, flmsg_text, full_report, js8call_text
from s2_watch.locating import ResolvedLocation
from s2_watch.models import Message

NOW = datetime(2026, 9, 29, 14, 52, tzinfo=timezone.utc)
FACTS = FollowUpFacts(
    message=Message("MagCommstat", "TULSA OK - RIVER FLOODING, 2 ROADS CLOSED — EVAC ORDERED",
                    received_at=datetime(2026, 9, 29, 12, 5, tzinfo=timezone.utc), fields={"sender": "KF5ABC"}),
    result=AnalysisResult(True, "Climate / Environment", "high", "Flooding in Tulsa, OK; roads closed", 0.9),
    locations=[ResolvedLocation("city", 36.154, -95.993, "Tulsa, OK", "OK", 6)],
    callsign="N1ROG", region=6, state="OK", copies=3, sources=("MagCommstat", "JS8Call"),
    first_seen=datetime(2026, 9, 29, 12, 5, tzinfo=timezone.utc), last_seen=datetime(2026, 9, 29, 13, 40, tzinfo=timezone.utc),
    official="✓ NWS Flood Warning", now=NOW,
)


def test_full_report_has_the_facts_in_labelled_lines() -> None:
    report = full_report(FACTS)
    assert dtg(NOW) == "291452Z SEP 26"
    for line in ("DTG: 291452Z SEP 26", "FROM: N1ROG", "PRIORITY: HIGH", "LOCATION: Tulsa, OK",
                 "AREA: OK, MARS Region 6", "FIRST HEARD: 291205Z SEP 26 / LAST HEARD: 291340Z SEP 26",
                 "SOURCE: MagCommstat (KF5ABC), 3 copies via MagCommstat, JS8Call",
                 "OFFICIAL CONFIRMATION: NWS Flood Warning"):
        assert line in report.splitlines(), line
    assert FACTS.message.raw_content in report


def test_radio_versions_come_from_the_edited_full_report() -> None:
    report = full_report(FACTS).replace("LOCATION: Tulsa, OK", "LOCATION: Sand Springs, OK")
    flmsg = flmsg_text(report)
    assert "LOCATION: SAND SPRINGS, OK" in flmsg and flmsg == flmsg.upper()
    assert all(len(line) <= 69 for line in flmsg.splitlines()) and flmsg.isascii()
    js8 = js8call_text(report)
    assert js8.startswith("S2 FOLLOWUP 291452Z SEP 26 HIGH CLIMATE / ENVIRONMENT - FLOODING IN TULSA")
    assert "AT SAND SPRINGS, OK" in js8 and "CONFIRMED NWS FLOOD WARNING" in js8 and js8.endswith("DE N1ROG")
    assert len(js8) <= 200 and ";" not in js8


def test_long_subjects_are_shortened_for_js8call() -> None:
    long = FollowUpFacts(**{**FACTS.__dict__, "result": AnalysisResult(True, "Infrastructure", "medium", "X " * 200, 0.5)})
    assert len(js8call_text(full_report(long))) <= 200
