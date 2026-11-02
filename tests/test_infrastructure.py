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
    headline = "Power outages hit neighborhood as crews investigate smoke from manholes"
    assert place(headline) is None
    assert place(headline, FOGGY_BOTTOM[1]) == "Washington, DC"     # "...-h-street-northwest-dc"


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


GOOGLE_FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Power outage reported near George Washington University - WUSA9</title>
<link>https://news.google.com/rss/articles/CBMiAAA</link><guid>g1</guid><pubDate>Mon, 02 Nov 2026 15:27:00 GMT</pubDate>
<source url="https://www.wusa9.com">WUSA9</source></item>
<item><title>Pepco outage map: Over 1,100 customers hit by power outage in Washington DC’s Foggy Bottom - Hindustan Times</title>
<link>https://news.google.com/rss/articles/CBMiBBB</link><guid>g2</guid><pubDate>Mon, 02 Nov 2026 15:54:08 GMT</pubDate>
<source url="https://www.hindustantimes.com">Hindustan Times</source></item>
<item><title>Power outage leaves 2,000 without power in Tulsa - KJRH</title>
<link>https://news.google.com/rss/articles/CBMiCCC</link><guid>g3</guid><pubDate>Mon, 02 Nov 2026 14:00:00 GMT</pubDate>
<source url="https://www.kjrh.com">KJRH</source></item>
<item><title>Tauranga outage: 994 properties affected - NZ Herald</title>
<link>https://news.google.com/rss/articles/CBMiDDD</link><guid>g4</guid><pubDate>Mon, 02 Nov 2026 14:00:00 GMT</pubDate>
<source url="https://www.nzherald.co.nz">NZ Herald</source></item>
<item><title>Russian drone strikes leave thousands of Ukrainians without power - TVP World</title>
<link>https://news.google.com/rss/articles/CBMiEEE</link><guid>g5</guid><pubDate>Mon, 02 Nov 2026 14:00:00 GMT</pubDate>
<source url="https://tvpworld.com">TVP World</source></item>
<item><title>La Palma in Spain's Canary Islands hit by power outage - TradingView</title>
<link>https://news.google.com/rss/articles/CBMiFFF</link><guid>g6</guid><pubDate>Mon, 02 Nov 2026 14:00:00 GMT</pubDate>
<source url="https://www.tradingview.com">TradingView</source></item>
</channel></rss>""".encode()


def test_google_news_brings_the_dc_outage_at_high_urgency_and_leaves_out_news_from_abroad(monkeypatch) -> None:
    monkeypatch.setattr(osint, "_get", lambda url, timeout=25: GOOGLE_FEED)
    monkeypatch.setattr(osint.world, "COVERAGE", {"us"})
    news = osint._google_news(osint.GOOGLE_INFRASTRUCTURE, infrastructure=True)
    assert [m.raw_content for m in news] == [
        "Power outage reported near George Washington University",          # the outlet's name dropped
        "Pepco outage map: Over 1,100 customers hit by power outage in Washington DC’s Foggy Bottom",
        "Power outage leaves 2,000 without power in Tulsa"]                 # New Zealand, Ukraine, Spain left out
    assert [(m.fields["urgency"], m.fields["sender"]) for m in news] == [
        ("high", "wusa9.com"), ("high", "hindustantimes.com"), ("low", "kjrh.com")]
    assert news[0].fields["source"] == "Google News" and news[0].received_at.isoformat() == "2026-11-02T15:27:00+00:00"
    assert place(news[1].raw_content) == "Washington, DC"


def test_google_news_is_a_switchable_source_fetched_every_half_hour() -> None:
    assert osint.FETCHERS["gnews"] is osint.fetch_google_news
    assert osint.SCHEDULE["gnews"] == 30 and "gnews" not in osint.DEFAULT_OFF
    assert "q=%22power+outage%22" in osint.google_news_url(osint.GOOGLE_INFRASTRUCTURE)


@pytest.mark.parametrize("text", ["Power outage hits Foggy Bottom", "Water main break in Anacostia",
                                  "Crash near Dupont Circle"])
def test_dc_neighbourhoods_found_nowhere_else_place_in_dc(text) -> None:
    assert place(text) == "Washington, DC"
