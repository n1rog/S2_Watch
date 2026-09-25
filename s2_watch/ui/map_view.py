import itertools
import json
from pathlib import Path
from html import escape

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.models import Message
from s2_watch.geo import maidenhead_to_coordinates
from s2_watch.locating import LocationResolver
from s2_watch.geocoding import NominatimGeocoder

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineSettings
except ImportError:
    QWebEngineView = None
    QWebChannel = None


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MARS_REGIONS_PATH = DATA_DIR / "mars_regions.geojson"
# Leaflet, the marker-cluster plugin and the offline base map ship with the app.
WEB_DIR = DATA_DIR / "web"
REGION_COLORS = (
    "#1565c0", "#2e7d32", "#c62828", "#6a1b9a", "#ef6c00",
    "#00838f", "#ad1457", "#4e342e", "#283593", "#558b2f",
)


def load_mars_regions() -> dict[str, object]:
    try:
        return json.loads(MARS_REGIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"type": "FeatureCollection", "features": []}


def mars_region_anchors() -> dict[int, tuple[float, float]]:
    return {
        int(feature["properties"]["region"]): tuple(feature["properties"]["anchor"])
        for feature in load_mars_regions()["features"]
        if feature["properties"].get("anchor")
    }


def mars_state_anchors() -> dict[tuple[int, str], tuple[str, tuple[float, float]]]:
    """Map (region, state abbreviation) to (state name, marker point) for state subgroups."""
    return {
        (int(feature["properties"]["region"]), abbreviation): (state["name"], tuple(state["anchor"]))
        for feature in load_mars_regions()["features"]
        for abbreviation, state in feature["properties"].get("states", {}).items()
    }


class MapBridge(QObject):
    message_selected = Signal(str)

    @Slot(str)
    def select_message(self, message_id: str) -> None:
        self.message_selected.emit(message_id)


class MapView(QWidget):
    CATEGORY_ICONS = {
        "Civil Unrest": ("⚠", "#8e24aa"),
        "Climate / Environment": ("⛈", "#2e7d32"),
        "Crime": ("🔒", "#c62828"),
        "Economy": ("$", "#1565c0"),
        "Health / Wellness": ("⚕", "#ad1457"),
        "Infrastructure": ("⚙", "#6d4c41"),
        "Military Conflict": ("⚔", "#263238"),
        "Politics": ("⚖", "#ef6c00"),
        "Supply Chain": ("🚚", "#00838f"),
        "Terrorism": ("☠", "#212121"),
        "routine": ("•", "#607d8b"),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._locations: list[dict[str, object]] = []
        self._enabled_applications: set[str] | None = None
        self._enabled_kinds = {"station", "news", "region"}
        self._resolver = LocationResolver()
        self._enabled_categories: set[str] | None = None
        self._radar_visible = False
        self._regions_visible = False
        self._online_tiles = True
        self._message_locations: dict[int, list[dict[str, object]]] = {}
        self._location_keys = itertools.count()
        self._page_ready = False
        self._web_view = QWebEngineView(self) if QWebEngineView else None
        if self._web_view:
            # The page is loaded from the bundled files but still fetches online tiles and radar.
            self._web_view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
        self._push_timer = QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(250)
        self._push_timer.timeout.connect(self._push_to_page)
        self._bridge = MapBridge(self)
        self._channel = QWebChannel(self._web_view) if self._web_view and QWebChannel else None
        if self._channel:
            self._channel.registerObject("mapBridge", self._bridge)
            self._web_view.page().setWebChannel(self._channel)
        self._fallback = QLabel("Map engine unavailable. Location markers will appear below.")
        self._fallback.setWordWrap(True)
        layout = QVBoxLayout(self)
        self._legend_widget = QWidget(self)
        legend = QGridLayout(self._legend_widget)
        legend.addWidget(QLabel("<b>Map icon legend</b>"), 0, 0, 1, 4)
        legend_categories = (*NEWS_CATEGORIES, "routine")
        for index, category in enumerate(legend_categories):
            symbol, color = self.CATEGORY_ICONS[category]
            icon = QLabel(symbol)
            icon.setFixedSize(28, 28)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet(
                f"background-color: {color}; color: white; border-radius: 14px; "
                "font-weight: bold; border: 1px solid white;"
            )
            label = QLabel(category)
            row, column = divmod(index, 2)
            legend.addWidget(icon, row + 1, column * 2)
            legend.addWidget(label, row + 1, column * 2 + 1)
        layout.addWidget(self._legend_widget)
        if self._web_view:
            layout.addWidget(self._web_view)
            self._web_view.loadFinished.connect(self._page_loaded)
            self._load_page()
        else:
            layout.addWidget(self._fallback)
            self._render()

    def set_legend_visible(self, visible: bool) -> None:
        self._legend_widget.setVisible(visible)

    def set_radar_visible(self, visible: bool) -> None:
        self._radar_visible = visible
        self._render()

    def set_regions_visible(self, visible: bool) -> None:
        self._regions_visible = visible
        self._render()

    def set_online_tiles(self, enabled: bool) -> None:
        self._online_tiles = enabled
        self._render()

    def set_geocoder(self, geocoder: NominatimGeocoder | None) -> None:
        self._resolver.geocoder = geocoder

    def set_filters(
        self,
        applications: set[str],
        kinds: set[str],
        categories: set[str],
    ) -> None:
        self._enabled_applications = set(applications)
        self._enabled_kinds = set(kinds)
        self._enabled_categories = set(categories)
        self._render()

    def add_message(self, message: Message, locations=None) -> None:
        """Add a message's markers; ``locations`` may be precomputed off the UI thread."""
        message_locations: list[dict[str, object]] = []
        grid = message.fields.get("grid") or message.fields.get("gridsquare")
        station = maidenhead_to_coordinates(grid) if grid else None
        if station:
            message_locations.append(
                {
                    "application": message.source_application,
                    "kind": "station",
                    "category": "routine",
                    "lat": station[0],
                    "lon": station[1],
                    "label": f"Station: {message.fields.get('sender', 'unknown')}",
                    "message": message.raw_content,
                    "message_id": str(id(message)),
                }
            )
        if locations is None:
            locations = self._resolver.resolve(message)
        for location in locations:
            if station and (location.lat, location.lon) == station:
                continue  # a StatRep's area is the station marker already shown
            label = location.label
            if location.kind == "region":
                label += " — general location"
            message_locations.append(
                {
                    "application": message.source_application,
                    "kind": location.kind,
                    "category": "routine",
                    "lat": location.lat,
                    "lon": location.lon,
                    "label": label,
                    "message": message.raw_content,
                    "message_id": str(id(message)),
                }
            )
        for location in message_locations:
            location["key"] = next(self._location_keys)
        self._message_locations[id(message)] = message_locations
        self._locations.extend(message_locations)
        if len(self._locations) > 500:
            self._locations = self._locations[-500:]
            retained = {id(location) for location in self._locations}
            self._message_locations = {
                message_key: locations
                for message_key, locations in self._message_locations.items()
                if any(id(location) in retained for location in locations)
            }
        self._render()

    def clear_locations(self) -> None:
        self._locations.clear()
        self._message_locations.clear()
        self._render()

    def set_message_category(self, message: Message, category: str) -> None:
        locations = self._message_locations.get(id(message), [])
        for location in locations:
            location["category"] = category
        if locations:
            self._render()

    def focus_message(self, message: Message) -> None:
        if not self._web_view:
            return
        self._web_view.page().runJavaScript(
            f"flashMessageMarker({json.dumps(str(id(message)))})"
        )

    def _render(self) -> None:
        if self._web_view:
            # Coalesce bursts of updates into one push to the already-loaded page.
            self._push_timer.start()
            return
        self._fallback.setText("\n".join(
            f"{location['application']} {location['kind']}: {location['label']} "
            f"({location['lat']}, {location['lon']})"
            for location in self._visible_locations()
        ) or "No locations detected yet.")

    def _visible_locations(self) -> list[dict[str, object]]:
        return [
            location for location in self._locations
            if (
                (self._enabled_applications is None
                 or location["application"] in self._enabled_applications)
                and location["kind"] in self._enabled_kinds
                and (
                    self._enabled_categories is None
                    or location["category"] in self._enabled_categories
                )
            )
        ]

    def _page_loaded(self, ok: bool) -> None:
        self._page_ready = ok
        if ok:
            self._push_to_page()

    def _push_to_page(self) -> None:
        if not self._web_view or not self._page_ready:
            return
        locations = json.dumps(self._visible_locations())
        radar = str(self._radar_visible).lower()
        regions = str(self._regions_visible).lower()
        tiles = str(self._online_tiles).lower()
        self._web_view.page().runJavaScript(
            f"setLocations({locations}); setRadarVisible({radar}); "
            f"setRegionsVisible({regions}); setOnlineTiles({tiles});"
        )

    def _load_page(self) -> None:
        html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="leaflet.css">
<link rel="stylesheet" href="MarkerCluster.css">
<style>
html,body,#map{{height:100%;margin:0}}
#map{{background:#aad3df}}
.place-label{{background:none;border:none}}
.place-label span{{display:block;transform:translate(-50%,-50%);white-space:nowrap;font:11px sans-serif;color:#37474f;text-shadow:0 0 2px #f2efe9,0 0 2px #f2efe9,0 0 2px #f2efe9}}
.category-marker span{{display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;color:white;font-weight:bold;font-size:15px;border:2px solid white;box-shadow:0 1px 4px #555}}
.radar-stamp{{background:rgba(255,255,255,.88);color:#1b1b1b;padding:3px 8px;border-radius:4px;font:12px sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.3)}}
.radar-stamp span{{color:#52514e}}
.radar-stamp .radar-step{{margin-left:4px;font-variant-numeric:tabular-nums}}
.region-label{{background:none;border:none}}
.region-label span{{display:block;transform:translate(-50%,-50%);white-space:nowrap;font:bold 13px sans-serif;color:#263238;text-shadow:0 0 3px white,0 0 3px white,0 0 3px white}}
.stack-cluster span{{display:flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;color:white;font:bold 13px sans-serif;border:3px solid white;box-shadow:0 0 0 2px rgba(0,0,0,.35),0 1px 5px #555}}
.stack-cluster.flash span,.category-marker.flash span{{animation:marker-flash 1.2s ease-in-out 3}}
@keyframes marker-flash{{50%{{transform:scale(1.8);box-shadow:0 0 18px 8px #ffeb3b}}}}
</style>
</head><body><div id="map"></div>
<script src="leaflet.js"></script>
<script src="leaflet.markercluster.js"></script>
<script src="basemap.js"></script>
<script src="counties.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
const categoryIcons={json.dumps(self.CATEGORY_ICONS)};
let mapBridge = null;
if (typeof QWebChannel !== 'undefined') {{
  new QWebChannel(qt.webChannelTransport, channel => {{ mapBridge = channel.objects.mapBridge; }});
}}
function markerIcon(category) {{
  const spec=categoryIcons[category] || categoryIcons["routine"];
  return L.divIcon({{className:"category-marker",html:'<span style="background:'+spec[1]+'">'+spec[0]+'</span>',iconSize:[26,26],iconAnchor:[13,13]}});
}}
const map=L.map('map').setView([39.5,-98.35],4);
// Offline base map: bundled vector data drawn beneath the online tiles, so it shows
// through wherever tiles cannot load, or on its own when online tiles are turned off.
map.createPane('basemap').style.zIndex = 150;
map.createPane('basemapLabels').style.zIndex = 160;
map.getPane('basemapLabels').style.pointerEvents = 'none';
const basemapRenderer = L.canvas({{pane:'basemap'}});
const basemapStyle = (style) => ({{renderer:basemapRenderer, interactive:false, ...style}});
const basemap = window.S2_BASEMAP || {{land:null, lakes:null, countries:null, states:null, places:[]}};
const basemapAttribution = 'Offline map: Natural Earth, US Census';
if (basemap.land) L.geoJSON(basemap.land, basemapStyle({{stroke:false, fillColor:'#f2efe9', fillOpacity:1, attribution:basemapAttribution}})).addTo(map);
if (basemap.lakes) L.geoJSON(basemap.lakes, basemapStyle({{stroke:false, fillColor:'#aad3df', fillOpacity:1}})).addTo(map);
const countyLayer = window.S2_COUNTIES
  ? L.geoJSON(window.S2_COUNTIES, basemapStyle({{color:'#b0a79b', weight:0.6, fill:false}}))
  : null;
if (basemap.states) L.geoJSON(basemap.states, basemapStyle({{color:'#8d8d8d', weight:1, dashArray:'4 3', fill:false}})).addTo(map);
if (basemap.countries) L.geoJSON(basemap.countries, basemapStyle({{color:'#6d6d6d', weight:1.4, fill:false}})).addTo(map);
const placeLabels = L.layerGroup().addTo(map);
function updateBasemapDetail() {{
  const zoom = map.getZoom();
  if (countyLayer) {{
    if (zoom >= 7 && !map.hasLayer(countyLayer)) countyLayer.addTo(map);
    if (zoom < 7 && map.hasLayer(countyLayer)) map.removeLayer(countyLayer);
  }}
  // Show more towns as the map zooms in (Natural Earth rank 0 = largest cities).
  placeLabels.clearLayers();
  const bounds = map.getBounds().pad(0.1);
  const maxRank = Math.max(1, Math.min(10, zoom * 2 - 6));
  let shown = 0;
  for (const [name, lat, lon, rank] of basemap.places) {{
    if (rank > maxRank || !bounds.contains([lat, lon])) continue;
    L.marker([lat, lon], {{pane:'basemapLabels', interactive:false, keyboard:false,
      icon:L.divIcon({{className:'place-label', html:'<span>'+escapeHtml(name)+'</span>', iconSize:[0,0]}})
    }}).addTo(placeLabels);
    if (++shown >= 250) break;
  }}
}}
map.on('zoomend moveend', updateBasemapDetail);
const onlineTiles = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{attribution:'© OpenStreetMap contributors'}}).addTo(map);
function setOnlineTiles(enabled) {{
  if (enabled && !map.hasLayer(onlineTiles)) onlineTiles.addTo(map);
  if (!enabled && map.hasLayer(onlineTiles)) map.removeLayer(onlineTiles);
}}
map.createPane('regions').style.zIndex = 350;
map.createPane('regionLabels').style.zIndex = 450;
map.getPane('regions').style.pointerEvents = 'none';
map.getPane('regionLabels').style.pointerEvents = 'none';
const marsRegions={json.dumps(load_mars_regions())};
const regionColors={json.dumps(REGION_COLORS)};
const regionLayer = L.layerGroup();
L.geoJSON(marsRegions, {{
  pane:'regions', interactive:false,
  style: f => {{
    const color = regionColors[(f.properties.region - 1) % regionColors.length];
    return {{color:color, weight:2, opacity:0.8, fillColor:color, fillOpacity:0.06}};
  }}
}}).addTo(regionLayer);
marsRegions.features.forEach(f => (f.properties.labels || []).forEach(point => {{
  L.marker(point, {{pane:'regionLabels', interactive:false, keyboard:false,
    icon:L.divIcon({{className:'region-label', html:'<span>'+escapeHtml(f.properties.name)+'</span>', iconSize:[0,0]}})
  }}).addTo(regionLayer);
}}));
function setRegionsVisible(visible) {{
  if (visible && !map.hasLayer(regionLayer)) regionLayer.addTo(map);
  if (!visible && map.hasLayer(regionLayer)) map.removeLayer(regionLayer);
}}
// Weather radar: RainViewer's last 2 hours (13 frames, 10 minutes apart) as a loop.
// Each frame's tile images are decoded once into ImageBitmaps and kept; the loop only
// redraws the map's canvas tiles from them. Letting the browser swap image layers
// instead (new layers per step, or fading prebuilt ones) re-decoded images endlessly and
// grew memory until the map was killed. The frame list refreshes every 10 minutes.
const RADAR_HISTORY_SECONDS = 2 * 60 * 60;
let radarVisible = false;
let radarHost = '';
let radarFrames = [];            // [{{path, time}}], oldest first
let radarFrameIndex = 0;
let radarAnimation = null;
let radarRefresh = null;
const radarBitmaps = new Map();  // "path|z/x/y" -> Promise<ImageBitmap|null>
const RadarLayer = L.GridLayer.extend({{
  createTile(coords) {{
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 256;
    canvas.coords = coords;
    drawRadarTile(canvas);
    return canvas;
  }},
}});
const radarLayer = new RadarLayer({{opacity:0.55, attribution:'Radar © RainViewer'}});
radarLayer.on('tileunload', event => {{
  const suffix = '|' + event.coords.z + '/' + event.coords.x + '/' + event.coords.y;
  for (const [key, bitmap] of radarBitmaps) {{
    if (key.endsWith(suffix)) {{
      bitmap.then(image => image && image.close());
      radarBitmaps.delete(key);
    }}
  }}
}});
function radarBitmap(path, coords) {{
  const key = path + '|' + coords.z + '/' + coords.x + '/' + coords.y;
  if (!radarBitmaps.has(key)) {{
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.src = radarHost + path + '/256/' + coords.z + '/' + coords.x + '/' + coords.y + '/2/1_1.png';
    radarBitmaps.set(key, image.decode().then(() => createImageBitmap(image)).catch(() => null));
  }}
  return radarBitmaps.get(key);
}}
function drawRadarTile(canvas) {{
  const frame = radarFrames[radarFrameIndex];
  if (!frame) return;
  radarFrames.forEach(other => radarBitmap(other.path, canvas.coords));  // decode every frame once
  radarBitmap(frame.path, canvas.coords).then(bitmap => {{
    if (radarFrames[radarFrameIndex] !== frame) return;
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, 256, 256);
    if (bitmap) context.drawImage(bitmap, 0, 0);
  }});
}}
function drawRadarFrame() {{
  for (const tile of Object.values(radarLayer._tiles || {{}})) drawRadarTile(tile.el);
  updateRadarStamp();
}}
const radarStamp = L.control({{position:'bottomleft'}});
radarStamp.onAdd = () => {{
  const element = L.DomUtil.create('div', 'radar-stamp');
  L.DomEvent.disableClickPropagation(element);
  return element;
}};
function updateRadarStamp() {{
  const element = radarStamp.getContainer();
  const frame = radarFrames[radarFrameIndex];
  if (!element) return;
  if (!frame) {{ element.textContent = 'Radar: loading…'; return; }}
  const when = new Date(frame.time * 1000);
  const minutes = Math.max(0, Math.round((Date.now() - when.getTime()) / 60000));
  const stamp = when.toLocaleString([], {{weekday:'short', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', timeZoneName:'short'}});
  const latest = radarFrameIndex === radarFrames.length - 1;
  element.innerHTML = '<b>Radar</b> ' + escapeHtml(stamp) + ' <span>' +
    (latest ? '(latest' + (minutes ? ', ' + minutes + ' min ago' : '') + ')' : '(' + minutes + ' min ago)') +
    '</span> <span class="radar-step">' + (radarFrameIndex + 1) + '/' + radarFrames.length + '</span>';
}}
function stepRadar() {{
  if (!radarFrames.length) return;
  // Hold on the newest frame for three steps so "now" is readable.
  stepRadar.hold = radarFrameIndex === radarFrames.length - 1 ? (stepRadar.hold || 0) + 1 : 0;
  if (stepRadar.hold && stepRadar.hold < 3) return;
  radarFrameIndex = (radarFrameIndex + 1) % radarFrames.length;
  drawRadarFrame();
}}
async function refreshRadarFrames() {{
  try {{
    const response = await fetch('https://api.rainviewer.com/public/weather-maps.json');
    const data = await response.json();
    if (!radarVisible) return;
    const cutoff = Math.floor(Date.now() / 1000) - RADAR_HISTORY_SECONDS;
    const frames = (data.radar && data.radar.past ? data.radar.past : []).filter(frame => frame.time >= cutoff);
    radarHost = data.host;
    const keep = new Set(frames.map(frame => frame.path));
    for (const [key, bitmap] of radarBitmaps) {{
      if (!keep.has(key.split('|')[0])) {{ bitmap.then(image => image && image.close()); radarBitmaps.delete(key); }}
    }}
    const showingLatest = radarFrameIndex >= radarFrames.length - 1;
    radarFrames = frames.map(frame => ({{path: frame.path, time: frame.time}}));
    radarFrameIndex = showingLatest ? radarFrames.length - 1 : Math.min(radarFrameIndex, radarFrames.length - 1);
    drawRadarFrame();
  }} catch (error) {{
    const element = radarStamp.getContainer();
    if (element) element.textContent = 'Radar unavailable (no network?)';
    console.error('Weather radar unavailable', error);
  }}
}}
function setRadarVisible(visible) {{
  if (visible === radarVisible) return;
  radarVisible = visible;
  if (visible) {{
    radarLayer.addTo(map);
    radarStamp.addTo(map);
    updateRadarStamp();
    refreshRadarFrames();
    radarAnimation = setInterval(stepRadar, 750);
    radarRefresh = setInterval(refreshRadarFrames, 10 * 60 * 1000);
    return;
  }}
  clearInterval(radarAnimation);
  clearInterval(radarRefresh);
  map.removeLayer(radarLayer);
  radarStamp.remove();
  for (const bitmap of radarBitmaps.values()) bitmap.then(image => image && image.close());
  radarBitmaps.clear();
  radarFrames = [];
}}
const markersByKey = new Map();
function clusterIcon(cluster) {{
  // Colour a stack by its most common newsworthy category; all-routine stacks stay neutral.
  const counts = {{}};
  cluster.getAllChildMarkers().forEach(m => {{
    const category = m.location.category;
    if (category !== 'routine') counts[category] = (counts[category] || 0) + 1;
  }});
  const top = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || 'routine';
  const color = (categoryIcons[top] || categoryIcons['routine'])[1];
  return L.divIcon({{className:'stack-cluster', html:'<span style="background:'+color+'">'+cluster.getChildCount()+'</span>', iconSize:[30,30], iconAnchor:[15,15]}});
}}
// Group only markers that overlap on screen (not merely nearby ones); clicking a group
// zooms in, or fans the markers out when they share one spot.
const markerLayer = (typeof L.markerClusterGroup === 'function')
  ? L.markerClusterGroup({{maxClusterRadius:14, iconCreateFunction:clusterIcon,
      showCoverageOnHover:false, spiderfyDistanceMultiplier:1.6}})
  : L.layerGroup();
markerLayer.addTo(map);
let messageMarkers = {{}};
function summaryPopup(p) {{
  return '<b>'+escapeHtml(p.category)+'</b><br>'+escapeHtml(p.kind)+'<br>'+escapeHtml(p.application)+'<br>'+escapeHtml(p.label);
}}
function addMarker(p) {{
  const marker = L.marker([p.lat,p.lon],{{icon:markerIcon(p.category)}}).bindPopup(summaryPopup(p));
  marker.location = p;
  marker.on('click', function() {{
    flashMarker(marker);
    if (mapBridge && marker.location.message_id) mapBridge.select_message(marker.location.message_id);
  }});
  marker.on('dblclick', function() {{
    const q = marker.location;
    const details = '<b>Newsworthy message</b><br>' +
      '<b>Application:</b> ' + escapeHtml(q.application) + '<br>' +
      '<b>Category:</b> ' + escapeHtml(q.category) + '<br>' +
      '<b>Location:</b> ' + escapeHtml(q.label) + '<br><br>' +
      '<div style="white-space:pre-wrap">' + escapeHtml(q.message) + '</div>';
    this.setPopupContent(details).openPopup();
  }});
  return marker;
}}
function setLocations(locations) {{
  const wanted = new Set(locations.map(p => p.key));
  const removed = [];
  for (const [key, marker] of markersByKey) {{
    if (!wanted.has(key)) {{
      removed.push(marker);
      markersByKey.delete(key);
    }}
  }}
  if (removed.length) removed.forEach(marker => markerLayer.removeLayer(marker));
  messageMarkers = {{}};
  const added = [];
  const recoloured = [];
  locations.forEach(p => {{
    let marker = markersByKey.get(p.key);
    if (!marker) {{
      marker = addMarker(p);
      markersByKey.set(p.key, marker);
      added.push(marker);
    }} else if (marker.location.category !== p.category) {{
      marker.location = p;
      marker.setIcon(markerIcon(p.category));
      if (!marker.isPopupOpen()) marker.setPopupContent(summaryPopup(p));
      recoloured.push(marker);
    }}
    if (p.message_id) (messageMarkers[p.message_id] = messageMarkers[p.message_id] || []).push(marker);
  }});
  if (added.length) {{
    if (markerLayer.addLayers) markerLayer.addLayers(added);
    else added.forEach(marker => markerLayer.addLayer(marker));
  }}
  if (recoloured.length && markerLayer.refreshClusters) markerLayer.refreshClusters(recoloured);
}}
function flashMarker(marker) {{
  // A marker inside a group has no element of its own; flash the group instead.
  const shown = markerLayer.getVisibleParent ? (markerLayer.getVisibleParent(marker) || marker) : marker;
  const element = shown.getElement ? shown.getElement() : null;
  if (!element) return;
  element.classList.remove('flash');
  void element.offsetWidth;
  element.classList.add('flash');
}}
function flashMessageMarker(messageId) {{
  (messageMarkers[messageId] || []).forEach(flashMarker);
}}
function escapeHtml(value) {{
  return String(value ?? '').replace(/[&<>"']/g, character => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[character]));
}}
updateBasemapDetail();
</script></body></html>"""
        self._web_view.setHtml(html, QUrl.fromLocalFile(str(WEB_DIR) + "/"))
