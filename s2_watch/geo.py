import re


# Both numbers need decimals so dates ("10/12") and counts ("5, 10") are not coordinates.
_COORDINATE_PATTERN = re.compile(
    r"(?<![\d.])(-?\d{1,3}\.\d+)\s*[,;/]\s*(-?\d{1,3}\.\d+)(?![\d.])"
)
_GRID_PATTERN = re.compile(r"\b([A-Ra-r]{2}\d{2}(?:[A-Xa-x]{2})?)\b")
# MARS region codes as used by feeder groups and rosters: MR01-MR10, also MR1, mr06, @MR06.
_MARS_REGION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])MR-?0?(10|[1-9])(?:([A-Za-z]{2})(?![A-Za-z0-9]))?(?![0-9])", re.IGNORECASE
)
# MR01-MR10 (optionally with a state suffix such as MR06OK) also parse as Maidenhead grids
# near the North Pole; in feeder text they are always MARS region groups.
_MARS_GRID_LOOKALIKE = re.compile(r"MR(?:0[1-9]|10)(?:[A-Za-z]{2})?", re.IGNORECASE)


def maidenhead_to_coordinates(grid: str) -> tuple[float, float] | None:
    value = grid.strip().upper()
    if len(value) not in (4, 6) or not _GRID_PATTERN.fullmatch(value):
        return None
    lon = (ord(value[0]) - ord("A")) * 20 - 180
    lat = (ord(value[1]) - ord("A")) * 10 - 90
    lon += int(value[2]) * 2
    lat += int(value[3])
    lon_width, lat_height = 2.0, 1.0
    if len(value) == 6:
        lon += (ord(value[4]) - ord("A")) * (5 / 60)
        lat += (ord(value[5]) - ord("A")) * (2.5 / 60)
        lon_width, lat_height = 5 / 60, 2.5 / 60
    return lat + lat_height / 2, lon + lon_width / 2


def extract_coordinates(text: str) -> list[tuple[float, float]]:
    coordinates = []
    for latitude, longitude in _COORDINATE_PATTERN.findall(text):
        lat, lon = float(latitude), float(longitude)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates.append((lat, lon))
    return coordinates


def extract_mars_region_references(text: str, fields: dict[str, str]) -> list[tuple[int, str | None]]:
    """Return (region, state suffix or None) for MR01-MR10 codes such as MR06 or MR06OK.

    A plain region reference is dropped when the same message also names a state
    subgroup of that region, since the subgroup is the more specific location.
    """
    references = []
    for value in (text, *fields.values()):
        for region, suffix in _MARS_REGION_PATTERN.findall(str(value)):
            references.append((int(region), suffix.upper() or None))
    references = list(dict.fromkeys(references))
    regions_with_state = {region for region, state in references if state}
    return [
        (region, state) for region, state in references
        if state or region not in regions_with_state
    ]


def extract_mars_regions(text: str, fields: dict[str, str]) -> list[int]:
    return list(dict.fromkeys(
        region for region, _state in extract_mars_region_references(text, fields)
    ))


def grids_in_text(text: str) -> list[str]:
    return list(dict.fromkeys(
        grid for grid in _GRID_PATTERN.findall(text)
        if not _MARS_GRID_LOOKALIKE.fullmatch(grid) and maidenhead_to_coordinates(grid)
    ))


def extract_message_locations(text: str, fields: dict[str, str]) -> list[tuple[float, float]]:
    locations = extract_coordinates(text)
    for key in ("grid", "gridsquare"):
        if fields.get(key):
            coordinates = maidenhead_to_coordinates(fields[key])
            if coordinates:
                locations.append(coordinates)
    for grid in _GRID_PATTERN.findall(text):
        if _MARS_GRID_LOOKALIKE.fullmatch(grid):
            continue
        coordinates = maidenhead_to_coordinates(grid)
        if coordinates:
            locations.append(coordinates)
    return list(dict.fromkeys(locations))
