"""Regenerate the offline map data in s2_watch/data/.

Inputs (public domain): Natural Earth 1:50m land, lakes, countries, states/provinces,
Natural Earth 1:10m populated places, and US Census county outlines (plotly's
geojson-counties-fips.json). Needs shapely at build time only:

    pip install --target /tmp/shapely shapely
    PYTHONPATH=/tmp/shapely python3 tools/build_basemap.py SOURCE_DIR

Writes:
  data/web/basemap.js     land, lakes, borders and city labels drawn under the map
  data/web/counties.js    US county outlines with FIPS codes, drawn when zoomed in and
                          used to draw NWS alerts issued for whole counties
  data/us_counties.json   county name, state and centre point, for locating messages
  data/places.json        cities and towns with population, for offline city lookup
"""
import json
import sys
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import polylabel

SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
DATA = Path(__file__).resolve().parent.parent / "s2_watch" / "data"
# North America plus margins: Alaska/Aleutians to Greenland, Hawaii to the Caribbean.
AREA = box(-180, 5, -50, 84)
COUNTRIES = {"USA", "CAN", "MEX"}
STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "72": "PR",
}


def rounded(value, digits=3):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, (list, tuple)):
        return [rounded(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    return value


def load(name):
    return json.loads((SOURCE / name).read_text(encoding="utf-8"))["features"]


def clipped(features, tolerance, keep=lambda properties: True, properties=lambda p: {}):
    out = []
    for feature in features:
        if not feature.get("geometry") or not keep(feature["properties"]):
            continue
        geometry = shape(feature["geometry"]).intersection(AREA)
        if geometry.is_empty:
            continue
        geometry = geometry.simplify(tolerance, preserve_topology=True)
        out.append({"type": "Feature", "properties": properties(feature["properties"]),
                    "geometry": rounded(json.loads(json.dumps(mapping(geometry), default=list)))})
    return {"type": "FeatureCollection", "features": out}


def write_js(path, variable, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"window.{variable}=" + json.dumps(value, separators=(",", ":")) + ";\n", encoding="utf-8")


def main():
    places = []
    for feature in load("ne_10m_populated_places_simple.geojson"):
        p = feature["properties"]
        if p.get("adm0_a3") not in COUNTRIES:
            continue
        places.append({
            "name": p["name"], "admin1": p.get("adm1name") or "", "country": p["adm0_a3"],
            "rank": int(p.get("scalerank", 10)), "population": int(p.get("pop_max") or 0),
            "lat": round(p["latitude"], 4), "lon": round(p["longitude"], 4),
        })
    basemap = {
        "land": clipped(load("ne_50m_land.geojson"), 0.02),
        "lakes": clipped(load("ne_50m_lakes.geojson"), 0.02, lambda p: (p.get("scalerank") or 0) <= 4),
        "countries": clipped(load("ne_50m_admin_0_countries.geojson"), 0.02),
        "states": clipped(
            load("ne_50m_admin_1_states_provinces_lakes.geojson"), 0.02,
            lambda p: p.get("adm0_a3") in COUNTRIES,
        ),
        "places": [[p["name"], p["lat"], p["lon"], p["rank"]] for p in places],
    }
    write_js(DATA / "web" / "basemap.js", "S2_BASEMAP", basemap)

    counties_geo, counties = [], []
    for feature in load("counties.json"):
        p = feature["properties"]
        state = STATE_FIPS.get(p["STATE"])
        if not state:
            continue
        geometry = shape(feature["geometry"])
        part = max(geometry.geoms, key=lambda g: g.area) if geometry.geom_type == "MultiPolygon" else geometry
        centre = polylabel(part, tolerance=0.005)
        counties.append({"name": p["NAME"], "kind": p.get("LSAD") or "County", "state": state,
                         "fips": p["STATE"] + p["COUNTY"], "lat": round(centre.y, 4), "lon": round(centre.x, 4)})
        simplified = geometry.simplify(0.005, preserve_topology=True)
        # FIPS code: NWS alerts name their counties by it (SAME codes).
        counties_geo.append({"type": "Feature", "properties": {"fips": p["STATE"] + p["COUNTY"]},
                             "geometry": rounded(json.loads(json.dumps(mapping(simplified), default=list)))})
    write_js(DATA / "web" / "counties.js", "S2_COUNTIES",
             {"type": "FeatureCollection", "features": counties_geo})
    (DATA / "us_counties.json").write_text(json.dumps(counties, separators=(",", ":")), encoding="utf-8")
    (DATA / "places.json").write_text(json.dumps(places, separators=(",", ":")), encoding="utf-8")
    print(f"{len(places)} places, {len(counties)} counties")


if __name__ == "__main__":
    main()
