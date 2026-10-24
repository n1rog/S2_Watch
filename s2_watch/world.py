"""The world beyond the U.S.: which country and world area a point is in, and the countries
and major cities a message names (data/world.json, built from Natural Earth by
tools/build_world.py).

The operator chooses which world areas S2 Watch covers (Configuration → Coverage); the
United States alone by default. U.S. places always come first: a message is placed abroad
only when it names no U.S. place.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from s2_watch.locating import _point_in_polygon, normalize

DATA = Path(__file__).resolve().parent / "data" / "world.json"
AREAS = (
    ("us", "United States"),
    ("north_america", "North America (Canada, Mexico, Greenland)"),
    ("central_america", "Central America and the Caribbean"),
    ("south_america", "South America"),
    ("europe", "Europe"),
    ("africa", "Africa"),
    ("asia", "Asia and the Middle East"),
    ("oceania", "Oceania (Australia, Pacific)"),
    ("antarctica", "Antarctica"),
)
AREA_NAMES = dict(AREAS)
SHORT_NAMES = {"us": "United States", "north_america": "North America", "central_america": "Central America",
               "south_america": "South America", "europe": "Europe", "africa": "Africa", "asia": "Asia",
               "oceania": "Oceania", "antarctica": "Antarctica"}
# Country names that are also everyday words, people or U.S. places: they count only after
# "in", "from", "near"... ("strikes in Lebanon", not "Lebanon, PA" or "Chad said").
AMBIGUOUS = {"CHAD", "JORDAN", "TURKEY", "JERSEY", "GUERNSEY", "GEORGIA", "GUINEA", "NIGER", "LEBANON", "PERU",
             "PANAMA", "CHINA", "MALI", "OMAN", "TOGO", "CUBA", "MONTSERRAT", "DOMINICA", "SAMOA", "ARUBA"}
# Spellings people write for cities Natural Earth names differently.
CITY_ALIASES = {"KYIV": "KIEV", "ODESA": "ODESSA", "KHARKOV": "KHARKIV", "YANGON": "RANGOON", "SAIGON": "HO CHI MINH CITY",
                "TEL AVIV": "TEL AVIV-YAFO", "BOMBAY": "MUMBAI", "PEKING": "BEIJING", "CALCUTTA": "KOLKATA",
                "MADRAS": "CHENNAI", "DNIPROPETROVSK": "DNIPRO", "KIEV": "KIEV"}
# A world city this many times bigger than every U.S. place of the same name (London, Paris,
# Moscow) counts without its country named; smaller ones (Athens) need the country.
DWARFS = 30
_LOCATIVE = re.compile(r"\b(?:IN|FROM|OF|NEAR|ACROSS|OVER|INTO|TO|INSIDE|THROUGHOUT|OFF)\s+$")
NEAREST_KM = 800        # a point at sea belongs to the area of the nearest coast this close

COVERAGE: set[str] = {"us"}


def set_coverage(areas) -> None:
    global COVERAGE
    COVERAGE = {area for area in areas if area in AREA_NAMES} or {"us"}


def covers(area: str | None) -> bool:
    """Whether the operator's coverage includes this area (None: unknown, counted as covered)."""
    return area is None or area in COVERAGE


def describe(areas=None) -> str:
    areas = COVERAGE if areas is None else areas
    return ", ".join(SHORT_NAMES[key] for key, _name in AREAS if key in areas)


def _distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


class World:
    def __init__(self, data: dict, us_place_sizes: dict[str, int] | None = None, state_codes=()) -> None:
        self.countries = data["countries"]
        self.by_code = {country["code"]: country for country in self.countries}
        for country in self.countries:
            polygons = [country["shape"]] if country["kind"] == "Polygon" else country["shape"]
            country["rings"] = [[[tuple(point) for point in ring] for ring in polygon] for polygon in polygons]
        names = {}
        for country in self.countries:
            for name in country["names"]:
                key = normalize(name)
                if key and key not in ("UNITED STATES", "UNITED STATES OF AMERICA", "USA"):
                    names.setdefault(key, country)
        self.country_names = names
        self._country_pattern = re.compile(
            r"\b(" + "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True)) + r")\b")
        cities: dict[str, list[dict]] = {}
        for city in data["cities"]:
            cities.setdefault(normalize(city["name"]), []).append(city)
        for alias, name in CITY_ALIASES.items():
            if normalize(name) in cities:
                cities[normalize(alias)] = cities[normalize(name)]
        self.cities = cities
        self.us_place_sizes = us_place_sizes or {}      # U.S. place name -> its largest population
        # "MOSCOW ID", "PARIS TX", "LEBANON PA": a U.S. place, never abroad.
        self._us_state_after = re.compile(r"^\s*,?\s*(?:" + "|".join(sorted(state_codes)) + r")\b") if state_codes else None
        self._city_pattern = re.compile(
            r"\b(" + "|".join(sorted((re.escape(n) for n in cities if len(n) > 2), key=len, reverse=True)) + r")\b")

    def country_at(self, lat: float, lon: float) -> dict | None:
        for country in self.countries:
            west, south, east, north = country["bbox"]
            if west <= lon <= east and south <= lat <= north and any(
                    _point_in_polygon(lat, lon, polygon) for polygon in country["rings"]):
                return country
        return None

    def area_of(self, lat: float, lon: float) -> str | None:
        """The world area of a point; at sea, that of the nearest coast within NEAREST_KM."""
        country = self.nearest_country(lat, lon)
        return country["area"] if country else None

    def nearest_country(self, lat: float, lon: float) -> dict | None:
        """The country a point is in; at sea, the one whose coast is nearest, within NEAREST_KM."""
        country = self.country_at(lat, lon)
        if country is not None:
            return country
        margin = NEAREST_KM / 111 + 1
        best, best_km = None, NEAREST_KM
        for country in self.countries:
            west, south, east, north = country["bbox"]
            if not (west - margin <= lon <= east + margin and south - margin <= lat <= north + margin):
                continue
            for polygon in country["rings"]:
                for ring in polygon:
                    for x, y in ring[::2]:          # every other vertex is close enough at this scale
                        km = _distance_km(lat, lon, y, x)
                        if km < best_km:
                            best, best_km = country, km
        return best

    def find(self, text: str):
        """The most specific place abroad a text names: (kind, name, lat, lon, country), kind
        "city" or "country"; or None."""
        upper = normalize(text)
        named = []
        for match in self._country_pattern.finditer(upper):
            name = match.group(1)
            if name in AMBIGUOUS and not _LOCATIVE.search(upper[max(0, match.start() - 12):match.start()]):
                continue
            if self._us_state_after and self._us_state_after.match(upper[match.end():]):
                continue
            named.append(self.country_names[name])
        named_codes = {country["code"] for country in named}
        for match in self._city_pattern.finditer(upper):
            if self._us_state_after and self._us_state_after.match(upper[match.end():]):
                continue
            options = self.cities[match.group(1)]
            # A name a U.S. place also has counts only with its country named, unless the
            # world city dwarfs the U.S. one (London, KY is not who's rioting).
            us_size = self.us_place_sizes.get(match.group(1))
            if us_size is not None:
                options = [city for city in options
                           if city["country"] in named_codes or city["pop"] >= DWARFS * max(us_size, 1)]
            elif named_codes:
                options = [city for city in options if city["country"] in named_codes] or options
            if options:
                city = max(options, key=lambda c: c["pop"])
                country = self.by_code.get(city["country"])
                return "city", city["name"], city["lat"], city["lon"], country
        if named:
            country = named[0]
            return "country", country["name"], country["label"][0], country["label"][1], country
        return None


def location_area(location) -> str | None:
    """The world area of a resolved location: the U.S. for U.S. states and MARS regions."""
    from s2_watch.locating import load_gazetteer

    if location.region or (location.state and location.state in load_gazetteer().state_names):
        return "us"
    return load_world().area_of(location.lat, location.lon)


@lru_cache(maxsize=1)
def load_world() -> World:
    from s2_watch.locating import load_gazetteer

    gazetteer = load_gazetteer()
    sizes = {name: max(place.get("population") or 0 for place in places)
             for name, places in gazetteer.places.items() if any(p["state"] for p in places)}
    for name in gazetteer.counties:
        sizes.setdefault(name, 100_000)     # a county name: treat as a sizeable U.S. place
    return World(json.loads(DATA.read_text(encoding="utf-8")), sizes, set(gazetteer.state_names))
