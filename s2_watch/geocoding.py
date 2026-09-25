import json
import re
import urllib.parse
import urllib.request


class GeocodingError(RuntimeError):
    pass


US_STATE_NAMES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}
US_STATE_PATTERN = re.compile(
    r"\b(" + "|".join(
        sorted((re.escape(state) for state in US_STATE_NAMES), key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)
PROPER_NAME_PATTERN = re.compile(
    r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b"
)
NON_LOCATION_WORDS = {
    "Environmental", "Emergency", "Conditions", "Severe", "Weather",
    "Flooding", "Reported", "Reports", "Warning", "Warnings", "Damage",
    "National", "United", "States",
}
US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY",
}
US_STATE_OR_ABBREVIATION = (
    r"(?:" + "|".join(sorted(
        (re.escape(state) for state in (*US_STATE_NAMES, *US_STATE_ABBREVIATIONS)),
        key=len,
        reverse=True,
    )) + r")"
)
CITY_STATE_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3}),\s*"
    + US_STATE_OR_ABBREVIATION + r"\b"
)
PLACE_PHRASE_PATTERN = re.compile(
    r"(?i:\b(?:near|in|at|around|outside|from))\s+"
    r"((?:[A-Z][A-Za-z.'-]*)(?:\s+[A-Z][A-Za-z.'-]*){0,3})\b",
)
REJECTED_RESULT_TYPES = {
    "continent", "country", "ocean", "sea", "region", "island",
}


def is_usable_geocode_result(result: dict[str, object]) -> bool:
    result_type = str(result.get("type", "")).casefold()
    result_class = str(result.get("class", "")).casefold()
    return result_type not in REJECTED_RESULT_TYPES and (
        result_class in {"boundary", "place"} or result_type == "administrative"
    )


def extract_place_candidates(text: str, fields: dict[str, str]) -> list[str]:
    candidates = []
    for key in ("location", "place", "city", "county", "state", "country"):
        value = fields.get(key, "").strip()
        if value:
            candidates.append(value)
    for match in PLACE_PHRASE_PATTERN.finditer(text):
        candidates.append(match.group(1).strip())
    for match in CITY_STATE_PATTERN.finditer(text):
        candidates.extend((match.group(1), match.group(0)))
    candidates.extend(
        candidate for match in PROPER_NAME_PATTERN.finditer(text)
        if (candidate := match.group(1)) not in NON_LOCATION_WORDS
    )
    candidates.extend(match.group(1) for match in US_STATE_PATTERN.finditer(text))
    return list(dict.fromkeys(candidates))


class NominatimGeocoder:
    """Opt-in geocoder using OpenStreetMap Nominatim with caller-provided caching."""

    def __init__(self, cache_get, cache_set, user_agent: str = "S2-Watch/0.1") -> None:
        self.cache_get = cache_get
        self.cache_set = cache_set
        self.user_agent = user_agent

    def geocode(self, place: str) -> tuple[float, float] | None:
        normalized = "v2:" + " ".join(place.split()).casefold()
        cached = self.cache_get(normalized)
        if cached is not None:
            return cached
        query = urllib.parse.urlencode({"q": place, "format": "jsonv2", "limit": 1})
        request = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{query}",
            headers={"User-Agent": self.user_agent},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                results = json.loads(response.read())
        except (OSError, ValueError) as error:
            raise GeocodingError(f"Geocoding failed for {place!r}: {error}") from error
        if not isinstance(results, list) or not results:
            self.cache_set(normalized, None)
            return None
        result = results[0]
        if not is_usable_geocode_result(result):
            self.cache_set(normalized, None)
            return None
        try:
            coordinates = (float(result["lat"]), float(result["lon"]))
        except (KeyError, TypeError, ValueError) as error:
            raise GeocodingError(f"Invalid geocoder response for {place!r}") from error
        self.cache_set(normalized, coordinates)
        return coordinates
