"""Regenerate s2_watch/data/world.json: countries (names, world area, outline) and major cities.

Inputs (public domain, Natural Earth): ne_50m_admin_0_countries.geojson and
ne_10m_populated_places_simple.geojson. Needs shapely at build time only:

    pip install --target /tmp/shapely shapely
    PYTHONPATH=/tmp/shapely python3 tools/build_world.py SOURCE_DIR [--land]

With --land it also writes data/web/world_land.js (needs ne_110m_land.geojson too): coarse
land and borders outside North America for the offline map.

S2 Watch uses it to place messages that name a country or a major city outside the U.S.
(the U.S. has its own, finer data), and to tell which world area a point is in.
"""
import json
import sys
from pathlib import Path

from shapely.geometry import mapping, shape

SOURCE = Path(next((a for a in sys.argv[1:] if not a.startswith("--")), "."))
OUT = Path(__file__).resolve().parent.parent / "s2_watch" / "data" / "world.json"
US_TERRITORIES = {"USA", "PRI", "GUM", "VIR", "ASM", "MNP", "UMI"}
NORTH_AMERICA = {"CAN", "MEX", "GRL", "BMU", "SPM"}
# Names people write that Natural Earth doesn't give.
ALIASES = {"GBR": ["UK", "Britain", "Great Britain", "England", "Scotland", "Wales"], "USA": ["United States"],
           "RUS": ["Russian Federation"], "KOR": ["South Korea", "Republic of Korea"], "PRK": ["North Korea", "DPRK"],
           "COD": ["DR Congo", "DRC", "Congo-Kinshasa"], "COG": ["Congo-Brazzaville"], "CZE": ["Czech Republic"],
           "CIV": ["Ivory Coast"], "MMR": ["Burma"], "PSE": ["Gaza", "West Bank", "Palestinian Territories"],
           "ARE": ["UAE", "Emirates"], "TWN": ["Taiwan"], "SYR": ["Syria"], "IRN": ["Iran"], "VNM": ["Vietnam"],
           "LAO": ["Laos"], "BOL": ["Bolivia"], "VEN": ["Venezuela"], "TZA": ["Tanzania"], "MKD": ["North Macedonia"]}


def area(properties) -> str:
    code, continent, subregion = properties["ADM0_A3"], properties["CONTINENT"], properties["SUBREGION"]
    if code in US_TERRITORIES:
        return "us"
    if code in NORTH_AMERICA:
        return "north_america"
    if subregion in ("Central America", "Caribbean"):
        return "central_america"
    if continent.startswith("Seven seas"):
        if "Africa" in subregion:
            return "africa"
        if "Asia" in subregion:
            return "asia"
        return "antarctica" if properties["LABEL_Y"] < -45 else "oceania"
    return {"North America": "north_america", "South America": "south_america", "Europe": "europe", "Africa": "africa",
            "Asia": "asia", "Oceania": "oceania", "Antarctica": "antarctica"}[continent]


def rounded(value):
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, (list, tuple)):
        return [rounded(item) for item in value]
    return value


def main() -> None:
    countries = []
    for feature in json.loads((SOURCE / "ne_50m_admin_0_countries.geojson").read_text(encoding="utf-8"))["features"]:
        p = feature["properties"]
        geometry = shape(feature["geometry"]).simplify(0.06, preserve_topology=True)
        names = {p["NAME"], p["NAME_LONG"], p["ADMIN"], *ALIASES.get(p["ADM0_A3"], [])}
        countries.append({
            "code": p["ADM0_A3"], "name": p["NAME"], "names": sorted(name for name in names if name),
            "area": area(p), "label": [round(p["LABEL_Y"], 3), round(p["LABEL_X"], 3)],
            "bbox": rounded(list(geometry.bounds)), "shape": rounded(mapping(geometry)["coordinates"]),
            "kind": mapping(geometry)["type"],
        })
    cities = []
    for feature in json.loads((SOURCE / "ne_10m_populated_places_simple.geojson").read_text(encoding="utf-8"))["features"]:
        p = feature["properties"]
        if p["adm0_a3"] == "USA":
            continue                       # the U.S. has its own finer place data
        if (p.get("pop_max") or 0) < 200_000 and not p.get("adm0cap"):
            continue
        cities.append({"name": p["nameascii"] or p["name"], "country": p["adm0_a3"], "pop": int(p.get("pop_max") or 0),
                       "lat": round(p["latitude"], 3), "lon": round(p["longitude"], 3),
                       "capital": bool(p.get("adm0cap"))})
    OUT.write_text(json.dumps({"countries": countries, "cities": cities}, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(countries)} countries, {len(cities)} cities, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()


def build_world_land(source: Path = SOURCE) -> None:
    """data/web/world_land.js: coarse land and country borders outside the detailed North
    America base map, so the offline map isn't blank elsewhere."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    detailed = box(-180, 5, -50, 84)        # what data/web/basemap.js already covers
    land = [shape(f["geometry"]).difference(detailed)
            for f in json.loads((source / "ne_110m_land.geojson").read_text(encoding="utf-8"))["features"]]
    borders = unary_union([shape(f["geometry"]).simplify(0.1, preserve_topology=True).boundary
                           for f in json.loads((source / "ne_50m_admin_0_countries.geojson").read_text(encoding="utf-8"))["features"]])
    borders = borders.difference(detailed)

    def collection(geometries):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": g.geom_type, "coordinates": rounded(mapping(g)["coordinates"])}}
            for g in geometries if not g.is_empty]}

    out = OUT.parent / "web" / "world_land.js"
    data = {"land": collection(land), "borders": collection([borders])}
    out.write_text("window.S2_WORLD_LAND=" + json.dumps(data, separators=(",", ":")) + ";", encoding="utf-8")
    print(f"{out}: {out.stat().st_size // 1024} KB")


if __name__ == "__main__" and "--land" in sys.argv:
    build_world_land()
