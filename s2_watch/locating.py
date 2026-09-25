"""Decide where a message is about, using the most specific location it names.

Levels, most specific first; the first level that yields a location wins and every
location found at that level is returned:

    precise  latitude/longitude pairs and 6-character grid squares
    city     cities and towns
    county   counties, parishes and boroughs
    grid     4-character grid squares
    state    states (names, "City, ST", MR06OK-style suffixes)
    region   MARS regions (MR01-MR10)

Broader levels also narrow the narrower ones: a state or MARS region named in the
same message decides which "Springfield" or "Washington County" is meant, and a
geocoded town outside the named region is rejected. For StatReps, which report on
the sender's own area, the sender's grid square counts at its grid level.

Matching is case-insensitive because much radio traffic is all capitals, so place
names are looked up in bundled gazetteers rather than spotted by capitalisation.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from s2_watch.geo import (
    extract_coordinates,
    extract_mars_region_references,
    grids_in_text,
    maidenhead_to_coordinates,
)
from s2_watch.dedup import strip_envelope
from s2_watch.geocoding import GeocodingError
from s2_watch.models import Message

DATA_DIR = Path(__file__).resolve().parent / "data"
LEVELS = ("precise", "city", "county", "grid", "state", "region")
# Levels shown under the map's "General locations" filter rather than "News locations".
GENERAL_LEVELS = {"state", "region"}

_WORD = r"[A-Z][A-Z.'-]*"
_LOCATIVE = re.compile(
    r"\b(?:IN|NEAR|AT|AROUND|OUTSIDE(?:\s+OF)?|FROM|OF|TO|ACROSS|THROUGH)\s+"
    rf"({_WORD}(?:\s+{_WORD}){{0,2}})"
)
_COUNTY = re.compile(rf"\b({_WORD}(?:\s+{_WORD}){{0,2}})\s+(?:COUNTY|CNTY|CO\.|PARISH|BOROUGH)(?![A-Z])")
_COUNTY_SUFFIX = re.compile(r"\s(?:COUNTY|CNTY|CO\.?|PARISH|BOROUGH)$")
# Status lines usually open with the state: "MO - FLOOD WATCH", "AZ: Dust engulfs ...".
_STATE_PREFIX = re.compile(r"^\s*([A-Z]{2})\s*[-–:]")
# In StatReps and CommStat status lines (",EM12JV,1,X23,+,TX USUAL RIG") the state
# opens the text even without punctuation.
_STATE_WORD_PREFIX = re.compile(r"^\s*([A-Z]{2})\s+[A-Z]")
_STATUS_LINE = re.compile(r",[A-R]{2}[0-9]{2}(?:[A-X]{2})?,\d+,[A-Z0-9]{3},", re.IGNORECASE)
# Words that continue a longer (often Spanish) place name after a US town's name.
_NAME_CONTINUATIONS = {"DEL", "DE", "LA", "LAS", "LOS", "EL", "DA", "DO", "DI"}
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PREPOSITIONS = re.compile(r"\b(?:IN|NEAR|AT|AROUND|OUTSIDE|OF|FROM|TO|ACROSS|THROUGH|AND)\b")


@dataclass(frozen=True)
class ResolvedLocation:
    level: str
    lat: float
    lon: float
    label: str
    state: str | None = None     # two-letter abbreviation, when known
    region: int | None = None    # MARS region 1-10, when known

    @property
    def kind(self) -> str:
        return "region" if self.level in GENERAL_LEVELS else "news"


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    text = re.sub(r"\bSAINT\b", "ST", text)
    text = re.sub(r"\bSAINTE\b", "STE", text)
    text = re.sub(r"[.'`]", "", text)
    return " ".join(text.replace("-", " ").split())


@dataclass
class Gazetteer:
    state_names: dict[str, str]                      # "OK" -> "Oklahoma"
    state_by_name: dict[str, str]                    # "OKLAHOMA" -> "OK"
    state_region: dict[str, int]                     # "OK" -> 6
    state_anchor: dict[str, tuple[float, float]]
    region_anchor: dict[int, tuple[float, float]]
    region_shapes: dict[int, list[list[list[tuple[float, float]]]]]
    counties: dict[str, list[dict]]                  # normalized name -> entries
    places: dict[str, list[dict]]                    # normalized name -> entries
    _point_cache: dict = field(default_factory=dict)

    def in_region(self, region: int, lat: float, lon: float) -> bool:
        return any(_point_in_polygon(lat, lon, polygon) for polygon in self.region_shapes.get(region, []))

    def locate_point(self, lat: float, lon: float) -> tuple[str | None, int | None]:
        """State (nearest county centre, if close) and MARS region of a coordinate."""
        key = (round(lat, 2), round(lon, 2))
        if key not in self._point_cache:
            nearest = min(
                (county for entries in self.counties.values() for county in entries),
                key=lambda county: (county["lat"] - lat) ** 2 + (county["lon"] - lon) ** 2,
            )
            close = (nearest["lat"] - lat) ** 2 + (nearest["lon"] - lon) ** 2 <= 1.5 ** 2
            state = nearest["state"] if close else None
            region = self.state_region.get(state) if state else None
            if region is None:
                region = next((r for r in self.region_shapes if self.in_region(r, lat, lon)), None)
            self._point_cache[key] = (state, region)
        return self._point_cache[key]

    def with_area(self, location: "ResolvedLocation") -> "ResolvedLocation":
        """Fill in a location's state and region where the level did not already give them."""
        if location.state or location.region:
            region = location.region or self.state_region.get(location.state or "")
            return ResolvedLocation(location.level, location.lat, location.lon, location.label,
                                    location.state, region)
        state, region = self.locate_point(location.lat, location.lon)
        return ResolvedLocation(location.level, location.lat, location.lon, location.label, state, region)


def _point_in_polygon(lat: float, lon: float, rings: list[list[tuple[float, float]]]) -> bool:
    inside = False
    for ring in rings:  # even-odd rule also handles holes
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


@lru_cache(maxsize=1)
def load_gazetteer() -> Gazetteer:
    regions = json.loads((DATA_DIR / "mars_regions.geojson").read_text(encoding="utf-8"))
    state_names, state_region, state_anchor, region_anchor, region_shapes = {}, {}, {}, {}, {}
    for feature in regions["features"]:
        props = feature["properties"]
        region = int(props["region"])
        region_anchor[region] = tuple(props["anchor"])
        for abbreviation, state in props.get("states", {}).items():
            state_names[abbreviation] = state["name"]
            state_region[abbreviation] = region
            state_anchor[abbreviation] = tuple(state["anchor"])
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        region_shapes[region] = [[[tuple(point) for point in ring] for ring in polygon] for polygon in polygons]
    state_by_name = {name.upper(): abbreviation for abbreviation, name in state_names.items()}

    counties: dict[str, list[dict]] = {}
    for county in json.loads((DATA_DIR / "us_counties.json").read_text(encoding="utf-8")):
        counties.setdefault(normalize(county["name"]), []).append(county)
    places: dict[str, list[dict]] = {}
    for place in json.loads((DATA_DIR / "places.json").read_text(encoding="utf-8")):
        state = state_by_name.get(place["admin1"].upper())
        if place["country"] == "USA" and state:
            places.setdefault(normalize(place["name"]), []).append({**place, "state": state})
    return Gazetteer(state_names, state_by_name, state_region, state_anchor, region_anchor,
                     region_shapes, counties, places)


def _state_names_pattern(gazetteer: Gazetteer) -> re.Pattern:
    names = sorted((re.escape(name) for name in gazetteer.state_by_name), key=len, reverse=True)
    # "Washington County" and "New York City" are not the states.
    return re.compile(r"\b(" + "|".join(names) + r")\b(?!\s+(?:COUNTY|CO\.|PARISH|CITY)\b)")


class LocationResolver:
    def __init__(self, geocoder=None) -> None:
        self.geocoder = geocoder
        self.gazetteer = load_gazetteer()
        self._state_pattern = _state_names_pattern(self.gazetteer)
        abbreviations = "|".join(self.gazetteer.state_names)
        names = "|".join(sorted((re.escape(n) for n in self.gazetteer.state_by_name), key=len, reverse=True))
        # One pattern for every bundled town name; names shared with a state ("NEW YORK")
        # need the "City, ST" form and are left out.
        town_names = sorted(
            (re.escape(name) for name in self.gazetteer.places if name not in self.gazetteer.state_by_name),
            key=len, reverse=True,
        )
        self._town_pattern = re.compile(r"\b(" + "|".join(town_names) + r")\b")
        self._city_state = re.compile(
            rf"\b({_WORD}(?:\s+{_WORD}){{0,3}}),\s*(?:({abbreviations})|({names}))\b"
        )

    def resolve(self, message: Message) -> list[ResolvedLocation]:
        fields = {key.casefold(): str(value) for key, value in message.fields.items()}
        # Numbers and words inside links are not locations.
        text = _URL.sub(" ", message.raw_content)
        upper = normalize_text(text)
        context = self._context(upper, text, fields)
        for level in LEVELS:
            found = getattr(self, f"_{level}")(upper, text, fields, context, message)
            if found:
                return [self.gazetteer.with_area(location) for location in dict.fromkeys(found)]
        return []

    # -- context: states and regions named anywhere in the message ---------------------

    def _context(self, upper: str, text: str, fields: dict[str, str]) -> dict:
        g = self.gazetteer
        states: list[str] = []
        city_state_pairs: list[tuple[str, str]] = []
        for match in self._city_state.finditer(upper):
            state = match.group(2) or g.state_by_name.get(match.group(3) or "", "")
            if state:
                # "FLOODING IN TULSA, OK" -> "TULSA"
                city = _PREPOSITIONS.split(match.group(1))[-1].strip()
                if city and not _COUNTY_SUFFIX.search(city):  # "TULSA COUNTY, OK" is a county
                    city_state_pairs.append((city, state))
                states.append(state)
        for match in self._state_pattern.finditer(upper):
            states.append(g.state_by_name[match.group(1)])
        body = normalize_text(strip_envelope(text))
        prefix = _STATE_PREFIX.match(body)
        status_line = "STATREP" in fields.get("type", "").upper() or _STATUS_LINE.search(text)
        if not prefix and status_line:
            prefix = _STATE_WORD_PREFIX.match(body)
        if prefix and prefix.group(1) in g.state_names:
            states.append(prefix.group(1))
        field_state = fields.get("state", "").strip().upper()
        if field_state in g.state_names:
            states.append(field_state)
        elif field_state in g.state_by_name:
            states.append(g.state_by_name[field_state])
        references = extract_mars_region_references(text, fields)
        suffix_states = [state for region, state in references
                         if state and g.state_region.get(state) == region]
        regions = list(dict.fromkeys(region for region, _state in references))
        return {
            "states": list(dict.fromkeys(states)),
            "suffix_states": list(dict.fromkeys(suffix_states)),
            "regions": regions,
            "references": references,
            "city_state_pairs": city_state_pairs,
        }

    def _allowed(self, context: dict, state: str) -> bool:
        states = context["states"] + context["suffix_states"]
        if states:
            return state in states
        if context["regions"]:
            return self.gazetteer.state_region.get(state) in context["regions"]
        return True

    def _constrained(self, context: dict) -> bool:
        return bool(context["states"] or context["suffix_states"] or context["regions"])

    # -- levels ------------------------------------------------------------------------

    def _precise(self, upper, text, fields, context, message):
        found = [ResolvedLocation("precise", lat, lon, "Coordinates in report")
                 for lat, lon in extract_coordinates(text)]
        for grid in grids_in_text(text):
            if len(grid) == 6:
                lat, lon = maidenhead_to_coordinates(grid)
                found.append(ResolvedLocation("precise", lat, lon, f"Grid {grid.upper()}"))
        found.extend(self._statrep_grid(fields, 6))
        return found

    def _grid(self, upper, text, fields, context, message):
        found = []
        for grid in grids_in_text(text):
            if len(grid) == 4:
                lat, lon = maidenhead_to_coordinates(grid)
                found.append(ResolvedLocation("grid", lat, lon, f"Grid {grid.upper()}"))
        found.extend(self._statrep_grid(fields, 4))
        return found

    def _statrep_grid(self, fields, length):
        if "STATREP" not in fields.get("type", "").upper():
            return []
        grid = (fields.get("grid") or fields.get("gridsquare") or "").strip()
        coordinates = maidenhead_to_coordinates(grid) if len(grid) == length else None
        if not coordinates:
            return []
        return [ResolvedLocation("precise" if length == 6 else "grid", *coordinates,
                                 f"StatRep area, grid {grid.upper()}")]

    def _city(self, upper, text, fields, context, message):
        g = self.gazetteer
        # "TULSA COUNTY" names the county, not the city.
        upper = _COUNTY.sub(" ", upper)
        # Strong candidates: explicit "City, ST", location fields, "in/near X" phrases.
        strong: list[tuple[str, str | None]] = [(city, state) for city, state in context["city_state_pairs"]]
        for key in ("city", "location", "place", "town"):
            if fields.get(key, "").strip():
                strong.append((normalize_text(fields[key]), None))
        for match in _LOCATIVE.finditer(upper):
            phrase = _PREPOSITIONS.split(match.group(1))[0].strip()
            if phrase:
                strong.append((phrase, None))

        # One candidate per name, keeping the one that names its state.
        by_name: dict[str, tuple[str, str | None]] = {}
        for phrase, state in strong:
            key = normalize(phrase)
            if key and (key not in by_name or (state and not by_name[key][1])):
                by_name[key] = (phrase, state)
        found: list[ResolvedLocation] = []
        unresolved: list[tuple[str, str | None]] = []
        for phrase, state in by_name.values():
            place = self._lookup_place(phrase, state, context, require_unique=state is None)
            if place:
                found.append(self._place_location(place))
            elif phrase.strip():
                unresolved.append((phrase, state))
        # Bare names: large cities anywhere, or any listed town inside a named state/region.
        plain = normalize(upper)
        for match in self._town_pattern.finditer(plain):
            name = match.group(1)
            following = plain[match.end():].split()
            if following and following[0] in _NAME_CONTINUATIONS:
                continue
            # "ENID OK": a town followed by its own state's code, with no comma.
            own_state = [e for e in g.places[name] if following and following[0] == e["state"]]
            if len(own_state) == 1:
                found.append(self._place_location(own_state[0]))
                continue
            candidates = [e for e in g.places[name] if self._allowed(context, e["state"])]
            if len(candidates) == 1 and (self._constrained(context) or candidates[0]["rank"] <= 3):
                found.append(self._place_location(candidates[0]))
        if not found and self.geocoder:
            found.extend(self._geocode(unresolved, context))
        return found

    def _lookup_place(self, phrase: str, state: str | None, context: dict, require_unique: bool):
        words = normalize(phrase).split()
        # Longest leading match first: "PORT ARTHUR TX" before "PORT".
        for length in range(len(words), 0, -1):
            name = " ".join(words[:length])
            entries = self.gazetteer.places.get(name)
            if not entries or (name in self.gazetteer.state_by_name and not state):
                continue
            if length < len(words) and words[length] in _NAME_CONTINUATIONS:
                return None  # "San Jose del Palmar" is not San Jose
            if state:
                entries = [e for e in entries if e["state"] == state]
            else:
                entries = [e for e in entries if self._allowed(context, e["state"])]
            if len(entries) == 1 or (entries and not require_unique):
                return max(entries, key=lambda e: e["population"])
            return None
        return None

    def _place_location(self, place: dict) -> ResolvedLocation:
        return ResolvedLocation("city", place["lat"], place["lon"], f"{place['name']}, {place['state']}",
                                place["state"])

    def _geocode(self, candidates, context) -> list[ResolvedLocation]:
        g = self.gazetteer
        found = []
        for phrase, state in candidates:
            name = phrase.title()
            states = [state] if state else (context["states"] + context["suffix_states"])[:1]
            query = f"{name}, {g.state_names[states[0]]}, USA" if states else f"{name}, USA"
            try:
                coordinates = self.geocoder.geocode(query)
            except GeocodingError:
                continue
            if not coordinates:
                continue
            if context["regions"] and not any(g.in_region(r, *coordinates) for r in context["regions"]):
                continue  # e.g. a same-named town outside the region the message is addressed to
            label = f"{name}, {states[0]}" if states else name
            found.append(ResolvedLocation("city", coordinates[0], coordinates[1], label))
        return found

    def _county(self, upper, text, fields, context, message):
        phrases = [match.group(1) for match in _COUNTY.finditer(upper)]
        if fields.get("county", "").strip():
            phrases.append(normalize_text(re.sub(r"(?i)\s+(county|parish|borough)$", "", fields["county"].strip())))
        found = []
        for phrase in phrases:
            words = normalize(phrase).split()
            # Longest trailing name first: "FLOODING IN ST LOUIS" -> "ST LOUIS".
            for start in range(len(words)):
                entries = self.gazetteer.counties.get(" ".join(words[start:]))
                if not entries:
                    continue
                entries = [e for e in entries if self._allowed(context, e["state"])]
                counties = [e for e in entries if e["kind"].casefold() != "city"] or entries
                if len(counties) == 1:
                    county = counties[0]
                    found.append(ResolvedLocation(
                        "county", county["lat"], county["lon"],
                        f"{county['name']} {county['kind']}, {county['state']}", county["state"],
                    ))
                break
        return found

    def _state(self, upper, text, fields, context, message):
        g = self.gazetteer
        return [
            ResolvedLocation("state", *g.state_anchor[state], f"{g.state_names[state]}", state)
            for state in dict.fromkeys(context["states"] + context["suffix_states"])
            if state in g.state_anchor
        ]

    def _region(self, upper, text, fields, context, message):
        g = self.gazetteer
        return [
            ResolvedLocation("region", *g.region_anchor[region], f"MARS Region {region} (MR{region:02d})",
                             None, region)
            for region in context["regions"] if region in g.region_anchor
        ]


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
