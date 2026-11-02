"""How urgent a news story about failing infrastructure (power, water, gas, bridges) is.

Where it happens matters more than how many customers it affects: an outage at a hospital,
an airport, a government or military site, or anywhere in the national capital is high
urgency however small; one in a state capital, or large or widespread, is medium; the rest
(a neighbourhood losing power for an hour) is low.
"""

from __future__ import annotations

import re

from s2_watch.locating import ResolvedLocation, normalize

CRITICAL_SITES = re.compile(
    r"\b(?:hospitals?|medical\s+centers?|nursing\s+homes?|airports?|air\s+force\s+bases?|military\s+bases?|"
    r"naval\s+(?:stations?|bases?)|army\s+(?:bases?|posts?)|national\s+guard|pentagon|white\s+house|capitol|"
    r"congress|supreme\s+court|federal\s+(?:buildings?|agenc(?:y|ies)|offices?|government)|"
    r"state\s+department|embass(?:y|ies)|courthouses?|city\s+hall|police\s+(?:stations?|headquarters)|"
    r"fire\s+stations?|911|emergency\s+(?:dispatch|operations|communications|rooms?)|dispatch\s+cent(?:er|re)s?|"
    r"water\s+(?:treatment|plants?|pumping)|wastewater|power\s+plants?|nuclear|data\s+cent(?:er|re)s?|"
    r"metro\s*(?:rail|stations?|trains?|service|system|lines?)|subways?|light\s+rail|union\s+station|"
    r"transit\s+(?:systems?|service|stations?)|amtrak|prisons?|jails?|ports?\s+of)\b",
    re.IGNORECASE)
LARGE_SCALE = re.compile(r"\b(?:hundreds\s+of\s+thousands|millions|state\s+of\s+emergency|rolling\s+blackouts?|"
                         r"grid\s+emergency|statewide|region\s*wide|multi-?state)\b", re.IGNORECASE)
WIDESPREAD = re.compile(r"\b(?:thousands|widespread|city\s*wide|county\s*wide|schools?|universit(?:y|ies)|"
                        r"campus(?:es)?)\b", re.IGNORECASE)
_COUNT = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand|million)?\s+(?:customers|homes|households|"
                    r"residents|people|businesses|accounts|meters|without\s+power)\b", re.IGNORECASE)

STATE_CAPITALS = {
    "AL": "Montgomery", "AK": "Juneau", "AZ": "Phoenix", "AR": "Little Rock", "CA": "Sacramento", "CO": "Denver",
    "CT": "Hartford", "DE": "Dover", "FL": "Tallahassee", "GA": "Atlanta", "HI": "Honolulu", "ID": "Boise",
    "IL": "Springfield", "IN": "Indianapolis", "IA": "Des Moines", "KS": "Topeka", "KY": "Frankfort",
    "LA": "Baton Rouge", "ME": "Augusta", "MD": "Annapolis", "MA": "Boston", "MI": "Lansing", "MN": "Saint Paul",
    "MS": "Jackson", "MO": "Jefferson City", "MT": "Helena", "NE": "Lincoln", "NV": "Carson City", "NH": "Concord",
    "NJ": "Trenton", "NM": "Santa Fe", "NY": "Albany", "NC": "Raleigh", "ND": "Bismarck", "OH": "Columbus",
    "OK": "Oklahoma City", "OR": "Salem", "PA": "Harrisburg", "RI": "Providence", "SC": "Columbia", "SD": "Pierre",
    "TN": "Nashville", "TX": "Austin", "UT": "Salt Lake City", "VT": "Montpelier", "VA": "Richmond", "WA": "Olympia",
    "WV": "Charleston", "WI": "Madison", "WY": "Cheyenne",
}
_CAPITALS = {(normalize(city).replace("SAINT ", "ST "), state) for state, city in STATE_CAPITALS.items()}


def customers(text: str) -> int:
    """The largest number of customers, homes or people the story says are affected."""
    most = 0
    for number, scale in _COUNT.findall(text):
        value = float(number.replace(",", "")) * {"k": 1e3, "thousand": 1e3, "million": 1e6}.get(scale.lower(), 1)
        most = max(most, int(value))
    return most


def state_capital(location: ResolvedLocation | None) -> bool:
    if location is None or location.level != "city" or not location.state:
        return False
    city = normalize(location.label.rsplit(",", 1)[0]).replace("SAINT ", "ST ")
    return (city, location.state) in _CAPITALS


def infrastructure_urgency(text: str, location: ResolvedLocation | None) -> tuple[str, str]:
    """(urgency, why) for a story about failing infrastructure at ``location``."""
    site = CRITICAL_SITES.search(text)
    if site:
        return "high", f"affects a critical site ({site.group(0).lower()})"
    if location is not None and location.state == "DC":
        return "high", "in the national capital"
    count = customers(text)
    large = LARGE_SCALE.search(text)
    if count >= 100_000 or large:
        return "high", f"{count:,} affected" if count >= 100_000 else large.group(0).lower()
    if state_capital(location):
        return "medium", f"in a state capital ({location.label})"
    wide = WIDESPREAD.search(text)
    if count >= 10_000 or wide:
        return "medium", f"{count:,} affected" if count >= 10_000 else wide.group(0).lower()
    return "low", "local"
