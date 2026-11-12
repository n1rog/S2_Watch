"""U.S. towns that news headlines name without their state.

"1 injured in shooting in East Providence", "Burned-up switch causes power outage in much of North
Platte", "Oldsmar Walmart evacuated": the Census Bureau's places of 2,500 people or more
(s2_watch/data/us_towns.json, built by tools/build_us_towns.py) place them.

A town's name alone isn't enough: Surprise, Justice and Mobile are towns too. A name counts
only when it is written as a name (capitalized, in text that isn't all capitals) and the words
around it say it's a place: "in/near/at X", or "X police/man/shooting/Walmart...". Where several
towns share the name, one in a state the text names wins, else one at least three times the
size of the next ("Brownsville" is Texas's, not Tennessee's); otherwise the name is left alone.
Names of places abroad (Paris, London, Athens) count only in a state the text names.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("data") / "us_towns.json"
DOMINANCE = 3            # a shared name goes to the largest town only if it is this many times the next
MAX_WORDS = 4

# Words before a place: "in East Providence", "near Pickens", "of North Platte" (of: two words or more).
BEFORE = {"in", "near", "at", "outside", "from", "across", "around", "into", "through", "throughout", "to",
          "toward", "towards", "downtown", "between", "of"}
# Words after one: "Oldsmar Walmart", "Bronx boy", "Brownsville shooting", "Pickens, city officials".
AFTER = {
    "police", "pd", "officer", "officers", "deputies", "sheriff", "firefighters", "crews", "authorities",
    "officials", "mayor", "council", "city", "schools", "school", "district", "hospital", "church", "neighborhood",
    "house", "apartment", "apartments", "business", "restaurant", "store", "walmart", "mall", "park", "bridge",
    "road", "street", "area", "community", "airport", "plant", "factory", "refinery", "jail", "courthouse",
    "shooting", "shootings", "shooter", "stabbing", "homicide", "murder", "killing", "crash", "collision",
    "explosion", "blast", "standoff", "robbery", "fire", "blaze", "outage", "outages", "protest", "protesters",
    "rally", "riot", "flooding", "flood", "tornado", "storm", "evacuation", "lockdown", "water", "power", "gas",
    "man", "woman", "men", "women", "teen", "teens", "teenager", "boy", "girl", "child", "children", "kids",
    "student", "students", "resident", "residents", "family", "families", "couple", "driver", "mother", "father",
}
# Towns whose names are everyday words in a headline's capitals ("Justice Department", "In Surprise Move").
NOT_PLACES = {"JUSTICE", "SURPRISE", "PARADISE", "LIBERTY", "COMMERCE", "ENTERPRISE", "OPPORTUNITY", "INDUSTRY",
              "SECURITY", "NORMAL", "UNITY", "HARMONY", "HOPE", "UNIVERSITY", "COLLEGE", "UNION", "CENTRAL",
              "WASHINGTON", "REPUBLIC", "MISSION", "FAIRVIEW", "MIDWAY", "VILLAGE", "HERITAGE", "PROGRESS",
              "NORTH", "SOUTH", "EAST", "WEST", "WHITE HOUSE", "OLD TOWN", "DOWNTOWN", "MIDTOWN", "CAPITOL", "CENTER", "CENTRE",
              "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
              "NOVEMBER", "DECEMBER"}
# Capitalized words that may come before a town's name without being part of it ("West Baltimore").
LEADING_WORDS = {"north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest", "downtown",
                 "central", "suburban", "rural", "greater", "fatal", "deadly"}
AGENCIES = {"police", "pd", "sheriff", "mayor", "council", "schools", "walmart"}
NAME_GOES_ON = {"del", "de", "la", "las", "los", "do", "da", "dos", "das", "di", "du", "le", "y"}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'.\-]*")
_SMALL_WORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "by", "with", "from", "as", "is"}


@lru_cache(maxsize=1)
def load_towns() -> dict[str, list]:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _word(token: str) -> str:
    return re.sub(r"'s$|’s$|\.$", "", token, flags=re.IGNORECASE) if not re.fullmatch(r"[A-Z][a-z]?\.", token) else token


def _capitalized(token: str) -> bool:
    return token[0].isupper()


def find_town(text: str, allowed=lambda state: True, named_states=(), abroad=lambda name: False):
    """The first town the text names as a place: (name as written, state, lat, lon), or None.

    ``allowed(state)``: whether a state fits what else the message says (its states or regions).
    ``named_states``: states the text names, which pick among towns of one name.
    ``abroad(name)``: whether the name is also a place abroad (needs a named state).
    """
    towns = load_towns()
    letters = [c for c in text if c.isalpha()]
    if not towns or not letters or sum(c.isupper() for c in letters) > 0.8 * len(letters):
        return None   # all capitals (radio traffic): names can't be told from other words
    tokens = [match.group(0) for match in _TOKEN.finditer(text)]
    long_words = [t for t in tokens if t.lower() not in _SMALL_WORDS and len(t) > 3]
    title_case = bool(long_words) and sum(_capitalized(t) for t in long_words) >= 0.8 * len(long_words)
    index = 0
    while index < len(tokens):
        for length in range(min(MAX_WORDS, len(tokens) - index), 0, -1):
            span = tokens[index:index + length]
            if not all(_capitalized(token) for token in span):
                continue
            words = span[:-1] + [_word(span[-1])]
            name = " ".join(words).upper()
            entries = towns.get(name)
            if not entries or name in NOT_PLACES:
                continue
            previous = tokens[index - 1] if index else ""
            if not title_case and index > 1 and previous[:1].isupper() and previous.lower() not in LEADING_WORDS \
                    and previous.lower() not in BEFORE:
                continue   # more of a name before it: "Lakewood Crest", "New Hampshire", "John Edwards"
            before = [t.lower() for t in tokens[max(0, index - 2):index]]
            if before and before[-1] == "the":
                before = before[:-1]
            after = tokens[index + length] if index + length < len(tokens) else ""
            after_word = _word(after).lower()
            # In Title Case Headlines every word is capitalized, so "In Surprise Move" says nothing:
            # only the word after counts there. A capitalized "Fire" names a fire ("Eaton Fire").
            cue_before = bool(before) and before[-1] in BEFORE and (before[-1] != "of" or length > 1) and not title_case
            # Capitalized, the word after is usually more of the name ("Morgan Park", "Yale Health
            # Center"), except an agency's ("Brownsville Police"); in Title Case every word is capitalized.
            cue_after = after_word in AFTER and (
                not after[:1].isupper() or after_word in AGENCIES or (title_case and after_word != "fire"))
            if (after[:1].isupper() and not cue_after) or after_word in NAME_GOES_ON:
                continue   # the name goes on: "Bell County", "Morgan Park", "Elon Musk", "San Jose del Palmar"
            if not (cue_before or cue_after):
                continue
            chosen = _choose(entries, allowed, named_states, abroad(name))
            if chosen:
                return " ".join(words), chosen[0], chosen[1], chosen[2]
        index += 1
    return None


def _choose(entries, allowed, named_states, abroad):
    in_named = [entry for entry in entries if entry[0] in named_states]
    if in_named:
        return in_named[0]
    if abroad:
        return None
    entries = [entry for entry in entries if allowed(entry[0])]
    if len(entries) == 1 or (entries and entries[0][3] >= DOMINANCE * entries[1][3]):
        return entries[0]
    return None


@lru_cache(maxsize=4096)
def _population_near(lat: float, lon: float, km: float) -> int:
    from s2_watch.events import distance_km

    return max((entry[3] for entries in load_towns().values() for entry in entries
                if abs(entry[1] - lat) < 0.5 and distance_km(lat, lon, entry[1], entry[2]) <= km), default=0)


def population_near(lat: float, lon: float, km: float = 5) -> int:
    """The population of the largest town within ``km`` of a point (0 if none): is it a city?"""
    return _population_near(round(lat, 3), round(lon, 3), km)
