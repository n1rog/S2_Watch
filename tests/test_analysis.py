from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.models import Message


def test_rules_analyzer_identifies_high_urgency_report() -> None:
    result = RuleBasedNewsAnalyzer().analyze(Message("JS8Call", "Flood evacuation near town"))
    assert result.newsworthy
    assert result.category == "Climate / Environment"
    assert result.urgency == "high"
    assert "flood" in result.matched_terms


def test_rules_analyzer_marks_routine_message() -> None:
    result = RuleBasedNewsAnalyzer().analyze(Message("JS8Call", "Good evening"))
    assert not result.newsworthy
    assert result.urgency == "low"


def test_rules_analyzer_uses_magcommstat_structured_fields() -> None:
    result = RuleBasedNewsAnalyzer().analyze(
        Message(
            "MagCommstat",
            "Reported conditions",
            fields={"type": "STATREP", "scope": "My Region", "remarks": "Road closed"},
        )
    )
    assert result.newsworthy
    assert result.category == "Infrastructure"
    assert result.summary.startswith("StatRep for My Region")


def test_heat_and_cold_products_are_newsworthy_weather() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    cases = {
        "MO - EXCESSIVE HEAT WARNING THRU TUES HEAT INDEX 105-110f": "high",
        "DANGEROUS COLD ON THE WAY IN WI": "high",
        "TX--Heat Advisory in Effect": "medium",
        "MI - BITTER COLD 1 0 F -13 F WINDCHILL LIGHT SNOW": "high",
        "SUB FREEZING TEMPS ALL WEEK": "medium",
        "KR0O: @MR05  FREEZE WARNING HERE": "medium",
        "6 INCHES OF SNOW SO FAR": "medium",
        "25th consecutive day with triple digits temps": "medium",
        "THUNDERSTORMS IN LLANO COUNTY": "medium",
    }
    for text, urgency in cases.items():
        result = analyzer.analyze(Message("MagCommstat", text))
        assert (result.newsworthy, result.category, result.urgency) == (True, "Climate / Environment", urgency), text


def test_plain_temperature_readings_and_small_talk_are_routine() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    for text in ("NY5V F!304 11111111 20F GUD HERE", "STAY WARM", "CARTONS OF ICE CREAM", "STILL GOT SNOW? LOL"):
        assert not analyzer.analyze(Message("JS8Call", text)).newsworthy, text


def test_terms_match_at_word_start_only() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    assert not analyzer.analyze(Message("JS8Call", "Patriot net check-in tonight")).newsworthy
    assert analyzer.analyze(Message("JS8Call", "Riot reported downtown")).category == "Civil Unrest"
    assert analyzer.analyze(Message("JS8Call", "Flooding on Main St")).category == "Climate / Environment"


def test_civil_unrest_urgency_and_false_friends() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    for text, urgency in {
        "CO - EX - WIDESPREAD LOOTING": "high",
        "police used significant force against May Day demonstrators including tear gas": "high",
        "CURFEW IN EFFECT 2200": "high",
        "CO - RAN INTO PROTEST DOWNTOWN COS": "medium",
        "GA/M4 MULTIPLE RALLIES IN THE ATLANTA AREA": "medium",
        "NO KINGS PROTESTS FOR 14 JUN CONFIRMED IN 30+ STATES": "medium",
    }.items():
        result = analyzer.analyze(Message("MagCommstat", text))
        assert (result.category, result.urgency) == ("Civil Unrest", urgency), text
    for text in ("THIS IS A TEST AND IS FOR DEMONSTRATION PURPOSES", "rally point at 1800", "HAPPY INDEPENDENCE DAY PATRIOTS"):
        assert analyzer.analyze(Message("JS8Call", text)).category != "Civil Unrest", text


def test_every_category_is_recognised_from_real_phrasing() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    cases = {
        "UNCONFIRM RPT OF A HIGH SCHOOL SHOOTING WARNER ROBINS GA": "Crime",
        "TN FUEL PRICES STILL ON THE RISE": "Economy",
        "Maricopa County health officials confirmed a new measles case": "Health / Wellness",
        "MID-TN PWR OUTAGES + ICEY RDS": "Infrastructure",
        "INDIA FIRES MISSILES INTO PAKISTAN": "Military Conflict",
        "EARLY VOTING LINE WAS 1HR 15 MIN LONG": "Politics",
        "GLOBAL OIL CHOKEPOINTS: HORMUZ FLOW DISPUTED": "Supply Chain",
        "MD WICOMICO CNTY: ALL ELEM SCHOOL BOMB THREAT": "Terrorism",
        "WI AIR QUALITY ALERT FOR WILDFIRE SMOKE": "Climate / Environment",
        "CO DENVER MAY DAY PROTEST 2PM-7PM": "Civil Unrest",
    }
    for text, category in cases.items():
        result = analyzer.analyze(Message("MagCommstat", text))
        assert result.newsworthy and result.category == category, (text, result.category)


def test_category_vocabulary_ignores_common_false_friends() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    for text in (
        "WHO HAS BBS SETUP? I WILL MAKE MYSELF A NOTE",
        "HELLO BILL TESTING",
        "S2 WEEKLY SNAP AND AIB ON VARAC BBS IF NEEDED",
        "MY 1ST ANTENNAS WERE SMALL & CRUDE",
        "AZ BREAK IN STORMS  MY STATION IS OTA",
        "DID ANY PACKAGE COME FROM CHINA?",
        "I HAVE N9TAX ANTENNA INCOMING FOR IT TO COMPLETE THE PORT STN",
        "MR01 NET STARTS AT TOP OF HR 01:00Z 7.115 900HZ",
        "WILL DEPLOY THE EFHW ANTENNA TONIGHT",
    ):
        assert not analyzer.analyze(Message("JS8Call", text)).newsworthy, text


def test_contained_wildfires_are_less_urgent() -> None:
    from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
    from s2_watch.models import Message

    rules = RuleBasedNewsAnalyzer()
    urgency = lambda text: rules.analyze(Message("OSINT", text)).urgency
    assert urgency("NIFC wildfire: Bouquet Fire — California; 1,000 acres, 0% contained") == "high"
    assert urgency("NIFC wildfire: Bull Fire — Nevada; 1,529 acres, 60% contained") == "medium"
    assert urgency("NIFC wildfire: Hagen Fire — Oregon; 61,380 acres, 99% contained") == "low"
