"""Build s2_watch/data/us_towns.json from the Census Bureau's 2010 places gazetteer.

Usage:
    curl -O https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_places_national.zip
    unzip Gaz_places_national.zip
    python tools/build_us_towns.py Gaz_places_national.txt

Every incorporated place and census-designated place with at least MIN_POPULATION people, by
name: {"NORTH PLATTE": [["NE", 41.13, -100.77, 24733]], ...}, largest first. The data is public
domain (U.S. Census Bureau). New York City's boroughs are added by hand: they aren't places.
"""

import csv
import json
import re
import sys
from pathlib import Path

MIN_POPULATION = 2500       # the Census Bureau's own line between urban and rural places
OUTPUT = Path(__file__).resolve().parents[1] / "s2_watch" / "data" / "us_towns.json"
# "Abbeville city", "Abanda CDP", "Nashville-Davidson metropolitan government (balance)" -> the name people use.
SUFFIX = re.compile(
    r"\s+(?:city|town|village|borough|CDP|municipality|plantation|comunidad|zona urbana|urban county"
    r"|consolidated government|metropolitan government|unified government|metro government|city and borough"
    r"|corporation)(?:\s+\(balance\))?$|\s+\(balance\)$", re.IGNORECASE)
BOROUGHS = {"BRONX": ["NY", 40.8448, -73.8648, 1385108], "BROOKLYN": ["NY", 40.6782, -73.9442, 2504700],
            "QUEENS": ["NY", 40.7282, -73.7949, 2230722], "MANHATTAN": ["NY", 40.7831, -73.9712, 1585873],
            "STATEN ISLAND": ["NY", 40.5795, -74.1502, 468730]}


def names(census_name: str) -> list[str]:
    """The names a place goes by: "Louisville/Jefferson County metro government (balance)" is
    Louisville, "Urban Honolulu CDP" Honolulu, "San Buenaventura (Ventura) city" both."""
    name = SUFFIX.sub("", census_name)
    if re.search(r"government|urban county|metro", census_name, re.IGNORECASE):
        name = re.split(r"[-/]", name)[0]
    name = re.sub(r"^Urban\s+", "", name)
    if name == "Boise City":                          # the census name of Idaho's capital
        return [name, "Boise"]
    alias = re.search(r"\s*\(([^)]+)\)$", name)
    if alias:
        return [name[:alias.start()], alias.group(1)]
    return [name]


def build(source: Path) -> dict[str, list]:
    towns: dict[str, list] = {}
    with source.open(encoding="latin-1", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            row = {key.strip(): value.strip() for key, value in row.items() if key}
            population = int(row["POP10"])
            if population < MIN_POPULATION:
                continue
            entry = [row["USPS"], round(float(row["INTPTLAT"]), 4), round(float(row["INTPTLONG"]), 4), population]
            for name in names(row["NAME"]):
                towns.setdefault(name.upper(), []).append(entry)
    for name, entry in BOROUGHS.items():
        towns.setdefault(name, []).append(entry)
    return {name: sorted(entries, key=lambda entry: -entry[3]) for name, entries in sorted(towns.items())}


if __name__ == "__main__":
    data = build(Path(sys.argv[1]))
    OUTPUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{len(data)} names written to {OUTPUT}")
