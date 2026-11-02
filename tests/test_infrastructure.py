"""Failing-infrastructure news: placing Washington, DC, and urgency by where it happens."""

import json
import urllib.error

import pytest

from s2_watch.adapters import osint
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.infrastructure import customers, infrastructure_urgency
from s2_watch.locating import LocationResolver
from s2_watch.models import Message

FOGGY_BOTTOM = ("Power outages hit Foggy Bottom as crews investigate smoke from manholes",
                "https://wjla.com/news/local/power-outages-hit-foggy-bottom-crews-investigate-smoke-from-manholes-"
                "h-street-northwest-dc")


def place(text, url=""):
    found = LocationResolver().resolve(Message("OSINT", text, fields={"type": "NEWS", "url": url} if url else {}))
    return found[0].label if found else None


@pytest.mark.parametrize("text", ["Outage in Washington, DC", "Outage in Washington DC", "Outage in Washington, D.C.",
                                  "Outage in D.C.", "Power out in northwest DC", "DC police close streets",
                                  "Washington Monument closed", "Fire near Washington Navy Yard"])
def test_washington_dc_is_the_city_not_the_state(text) -> None:
    assert place(text) == "Washington, DC"


@pytest.mark.parametrize("text, expected", [("12V DC power supply for sale", None), ("AC/DC concert tonight", None),
                                            ("Wildfire in eastern Washington", "Washington"),
                                            ("Flooding in Seattle, Washington", "Seattle, WA")])
def test_dc_and_washington_state_are_kept_apart(text, expected) -> None:
    assert place(text) == expected


def test_a_headline_naming_no_place_is_placed_by_its_storys_address() -> None:
    assert place(FOGGY_BOTTOM[0]) is None
    assert place(*FOGGY_BOTTOM) == "Washington, DC"


@pytest.mark.parametrize("headline, url, urgency", [
    (*FOGGY_BOTTOM, "high"),                                                     # the national capital, ~1,200 customers
    ("Blackout hits Walter Reed hospital in Bethesda", "", "high"),              # a critical site
    ("Gas leak closes Denver International Airport terminal", "", "high"),
    ("Storms knock out power to 250,000 customers across Texas", "", "high"),    # very large
    ("Thousands without power in Columbus, Ohio after storm", "", "medium"),     # a state capital
    ("Outage leaves 15,000 customers in the dark in Tulsa", "", "medium"),       # large
    ("Power outage leaves 2,000 without power in Tulsa", "", "low"),
    ("Water main break on Elm Street in Enid, Oklahoma", "", "low"),
])
def test_where_an_outage_is_matters_more_than_its_size(headline, url, urgency) -> None:
    location = osint._place(headline, url)
    assert infrastructure_urgency(headline, location)[0] == urgency


def test_customer_counts_are_read_in_their_usual_forms() -> None:
    assert customers("1,176 customers lost power") == 1176
    assert customers("outage hits 15k homes") == 15_000
    assert customers("1.2 million customers without power") == 1_200_000
    assert customers("power out since 2 pm") == 0


def test_an_infrastructure_story_is_infrastructure_news_at_its_locations_urgency() -> None:
    analyzer = RuleBasedNewsAnalyzer()
    for text, urgency in (("Pepco: 1,176 customers lost power in Northwest DC", "high"),
                          ("Blackout hits Walter Reed hospital in Bethesda", "high"),
                          ("Sinkhole swallows car in Bixby, Oklahoma", "low")):
        result = analyzer.analyze(Message("OSINT", text, fields={"type": "NEWS", "topic": "infrastructure",
                                                                 "urgency": urgency}))
        assert (result.newsworthy, result.category, result.urgency) == (True, "Infrastructure", urgency), text
    # Radio traffic keeps the rules' own urgency: the field is for the news search's stories.
    radio = analyzer.analyze(Message("JS8Call", "LOST POWER HERE IN TULSA", fields={"urgency": "low"}))
    assert (radio.category, radio.urgency) == ("Infrastructure", "medium")


def articles(*titles):
    return json.dumps({"articles": [{"title": title, "url": url, "domain": "example.com",
                                     "seendate": "20261102T150000Z"} for title, url in titles]}).encode()


def test_the_news_search_also_asks_for_infrastructure_stories(monkeypatch) -> None:
    asked = []

    def get(url, timeout=25):
        asked.append(url)
        if "outage" in url:
            return articles(FOGGY_BOTTOM, ("Protesters march downtown", "https://example.com/march"))
        return articles(("Protesters march downtown", "https://example.com/march"))

    monkeypatch.setattr(osint, "_get", get)
    monkeypatch.setattr(osint, "GDELT_PAUSE_S", 0)
    monkeypatch.setattr(osint.world, "COVERAGE", {"us"})
    news = osint.fetch_gdelt()
    assert len(asked) == 2
    assert [message.raw_content for message in news] == ["Protesters march downtown", FOGGY_BOTTOM[0]]   # once each
    outage = news[1].fields
    assert (outage["topic"], outage["urgency"], outage["urgency_reason"]) == (
        "infrastructure", "high", "in the national capital")

    def busy(url, timeout=25):
        if "outage" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return articles(("Protesters march downtown", "https://example.com/march"))

    monkeypatch.setattr(osint, "_get", busy)
    assert [message.raw_content for message in osint.fetch_gdelt()] == ["Protesters march downtown"]


def test_gdelts_plain_text_refusals_are_reported_in_its_own_words(monkeypatch) -> None:
    monkeypatch.setattr(osint, "_get", lambda url, timeout=25: b"Your query was too short or too long.\n")
    with pytest.raises(RuntimeError, match="GDELT: Your query was too short or too long."):
        osint._gdelt_news(osint.GDELT_INFRASTRUCTURE)
