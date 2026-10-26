from s2_watch.analysis.base import AnalysisResult
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.analysis.summary import is_placeholder_summary, summarize
from s2_watch.locating import LocationResolver
from s2_watch.models import Message


def headline(text: str, **fields) -> str:
    message = Message("MagCommstat", text, fields=fields)
    result = RuleBasedNewsAnalyzer().analyze(message)
    return summarize(message, result, LocationResolver().resolve(message))


def test_headline_has_event_place_and_facts() -> None:
    assert headline("MO - EXCESSIVE HEAT WARNING THRU TUES HEAT INDEX 105-110f") == \
        "Excessive heat warning — Missouri · heat index 105–110°F · through Tues"
    assert headline("UT - Black Canyon Fire.  3532 acres 0pct contained.  Evacuation orders in place.") == \
        "Black Canyon Fire — Utah · 3532 acres · 0% contained"
    assert headline("AZ: 1 dead and 2 injured in Buckeye shooting  suspect still on the loose") == \
        "Shooting — Arizona · 1 dead, 2 injured"
    assert headline("Hawaii- About 19k residents remain without power after hurricane Lala") == \
        "Hurricane Lala — Hawaii · 19k without power"


def test_headline_is_not_the_message_and_skips_the_radio_envelope() -> None:
    text = "W8UFO: @MAGNET MSG MD WICOMICO CNTY: ALL ELEM SCHOOL BOMB THREAT. LE PATROLS INCR *DE* W3DOF ♢"
    summary = headline(text)
    assert summary == "Bomb threat — Wicomico County, MD"
    assert "W8UFO" not in summary and "♢" not in summary


def test_foreign_place_names_are_not_us_towns() -> None:
    assert headline("Update magnitude 7.4 earthquake near San Jose del Palmar.") == "Earthquake · M7.4"


def test_routine_messages_get_a_short_gist() -> None:
    long_text = "KC7WOK: @MAGNET MSG GOOD MORNING ALL, NET TONIGHT AT 0100Z ON 7.115 WITH NCS KD9UEG AND RELAYS AS NEEDED ♢"
    assert headline(long_text) == "Good morning all, net tonight at 0100z on 7.115 with ncs kd9ueg…"


def test_only_placeholder_summaries_are_replaced() -> None:
    message = Message("JS8Call", "FLASH FLOOD WARNING FOR PIMA COUNTY")
    assert is_placeholder_summary(message, "StatRep for My Region: FLASH FLOOD WARNING FOR PIMA COUNTY")
    assert not is_placeholder_summary(message, "Flash flooding expected in Pima County, AZ tonight.")
    assert isinstance(AnalysisResult(True, "Crime", "high", "x", 0.5), AnalysisResult)


def test_a_heading_is_not_a_temperature() -> None:
    from s2_watch.analysis.summary import _facts

    assert "295°F" not in _facts("Tropical Storm Vance — 55 kt winds, moving 295° at 7 mph")[0]
    assert "270°F" not in _facts("Winds from 270 degrees at 15 kt")[0]
    assert "105°F" in _facts("Temperatures near 105° this afternoon")[0]
