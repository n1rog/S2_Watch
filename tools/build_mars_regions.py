"""Regenerate s2_watch/data/mars_regions.geojson (MARS regions = FEMA regions 1-10).

Merges the state outlines in FreqInOut's fema_regions.geojson into one outline per
region, checks every state against FEMA's published region list, and computes label
points. Needs shapely, which is only a build-time tool, not an app dependency:

    pip install --target /tmp/shapely shapely
    PYTHONPATH=/tmp/shapely python3 tools/build_mars_regions.py [path/to/fema_regions.geojson]
"""
import json, sys
from pathlib import Path
from shapely.geometry import shape, mapping, Point, Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.ops import polylabel
src = json.load(open(sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "MyApps/FreqInOut/config/leaflet/fema_regions.geojson")))
official = {
 1:"Connecticut Maine Massachusetts New_Hampshire Rhode_Island Vermont",
 2:"New_Jersey New_York",
 3:"Delaware Maryland Pennsylvania Virginia West_Virginia",
 4:"Alabama Florida Georgia Kentucky Mississippi North_Carolina South_Carolina Tennessee",
 5:"Illinois Indiana Michigan Minnesota Ohio Wisconsin",
 6:"Arkansas Louisiana New_Mexico Oklahoma Texas",
 7:"Iowa Kansas Missouri Nebraska",
 8:"Colorado Montana North_Dakota South_Dakota Utah Wyoming",
 9:"Arizona California Hawaii Nevada",
 10:"Alaska Idaho Oregon Washington"}
# Region 1's natural label point (New Hampshire) collides with Region 2's at national zoom.
LABEL_OVERRIDES = {1: [(45.3, -69.2)]}
STATE_ABBREVIATIONS = {
 "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA","Colorado":"CO",
 "Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA","Hawaii":"HI","Idaho":"ID",
 "Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS","Kentucky":"KY","Louisiana":"LA",
 "Maine":"ME","Maryland":"MD","Massachusetts":"MA","Michigan":"MI","Minnesota":"MN",
 "Mississippi":"MS","Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV",
 "New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY","North Carolina":"NC",
 "North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA",
 "Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD","Tennessee":"TN","Texas":"TX",
 "Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA","West Virginia":"WV",
 "Wisconsin":"WI","Wyoming":"WY"}
def clear_of_labels(point, area, labels):
    """Move a marker point vertically away from nearby region labels, staying inside area."""
    def clashes(p):
        return any(abs(p.y - l.y) < 1.6 and abs(p.x - l.x) < 3.0 for l in labels)
    if not clashes(point):
        return point
    nearest = min(labels, key=lambda l: abs(point.y - l.y) + abs(point.x - l.x))
    first = 1 if point.y >= nearest.y else -1
    for direction in (first, -first):
        for step in range(1, 20):
            candidate = Point(point.x, point.y + direction * step * 0.2)
            if not area.contains(candidate):
                break
            if not clashes(candidate):
                return candidate
    return point


expected = {s.replace("_"," "): r for r, names in official.items() for s in names.split()}
actual = {f["properties"]["name"]: int(f["properties"]["fema_region"]) for f in src["features"]}
bad = {k:(v,expected.get(k)) for k,v in actual.items() if expected.get(k)!=v}
print("mismatches vs official FEMA list:", bad or "none", "| states:", len(actual))
if bad: sys.exit(1)
features=[]
for region in range(1,11):
    geoms=[shape(f["geometry"]) for f in src["features"] if int(f["properties"]["fema_region"])==region]
    merged = unary_union([g.buffer(0.03) for g in geoms]).buffer(-0.03)
    polys = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]
    # Drop slivers and small interior gaps between neighbouring state outlines (e.g. DC).
    polys = [Polygon(p.exterior, [r for r in p.interiors if Polygon(r).area > 0.5]) for p in polys if p.area > 0.01]
    region_shape = MultiPolygon(polys).simplify(0.01, preserve_topology=True)
    polys.sort(key=lambda p: p.area, reverse=True)
    labels = [polylabel(p, tolerance=0.05) for i, p in enumerate(polys) if i == 0 or p.area > 20]
    if region in LABEL_OVERRIDES:
        labels = [Point(lon, lat) for lat, lon in LABEL_OVERRIDES[region]]
    # Markers for region-level references (MR01-MR10) sit just south of the label so they don't hide it.
    # Region 10 is labelled in Alaska and the Pacific Northwest; anchor on the southern (lower-48) label.
    anchor_label = min(labels, key=lambda l: l.y)
    anchor = Point(anchor_label.x, anchor_label.y - 1.2)
    if not any(p.contains(anchor) for p in polys):
        anchor = anchor_label
    # State-subgroup markers (e.g. MR06OK) sit at the state's visual centre, nudged south
    # when that would cover the region label.
    states = {}
    for f in src["features"]:
        if int(f["properties"]["fema_region"]) != region:
            continue
        geom = shape(f["geometry"])
        part = max(geom.geoms, key=lambda g: g.area) if isinstance(geom, MultiPolygon) else geom
        centre = polylabel(part, tolerance=0.02)
        centre = clear_of_labels(centre, part, labels)
        states[STATE_ABBREVIATIONS[f["properties"]["name"]]] = {
            "name": f["properties"]["name"], "anchor": [centre.y, centre.x]}
    features.append({"type":"Feature","properties":{"region":region,"name":f"Region {region}","labels":[[l.y,l.x] for l in labels],"anchor":[anchor.y,anchor.x],"states":states},
                     "geometry":json.loads(json.dumps(mapping(region_shape), default=list))})
    print(region, sorted(states), len(polys), "parts; holes:", sum(len(p.interiors) for p in polys), "labels", [(round(l.y,1), round(l.x,1)) for l in labels], "anchor", (round(anchor.y,1), round(anchor.x,1)))
def rnd(o):
    if isinstance(o,float): return round(o,3)
    if isinstance(o,(list,tuple)): return [rnd(x) for x in o]
    if isinstance(o,dict): return {k:rnd(v) for k,v in o.items()}
    return o
out={"type":"FeatureCollection","name":"MARS regions (FEMA regions 1-10)","features":rnd(features)}
json.dump(out, open("s2_watch/data/mars_regions.geojson","w"), separators=(",",":"))
