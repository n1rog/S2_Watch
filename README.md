# S2 Watch

S2 Watch is a cross-platform desktop monitor for messages from JS8Call and
future application adapters.

S2 Watch was created by N1ROG. Use **Help → About S2 Watch** to view the
application version and a short description.

The primary adapter connects to JS8Call's local TCP API, so the source
application can remain minimized while S2 Watch displays matching messages.
The text-log adapter remains available as a fallback for installations where
the TCP API is not enabled.

## Development

```bash
python -m venv .venv
. .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
python -m pytest
python -m s2_watch.main
```

## Building a distributable application

PyInstaller builds must be run on the target operating system. Build once on
Linux for Linux, Windows for Windows, and macOS for macOS.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[build]"
pyinstaller --clean s2_watch.spec
```

The packaged application is written to `dist/s2-watch/`. The application
stores its SQLite database in the current working directory unless a database
path is supplied by the embedding launcher. Do not package a live database
inside the executable; user data should remain writable and separate from the
installation.

For macOS, use the generated executable from the `.app` bundle when a windowed
application bundle is desired. For Windows, distribute the generated
directory or create an installer around it. For Linux, distribute the
generated directory or integrate it with the desktop environment.

The window persists JS8Call watch forms in SQLite. Forms can be added,
enabled/disabled, edited, and removed from the UI. Matching supports
case-insensitive text containment, regular expressions, JS8Call message type,
and sender/call-sign. The text-to-match field is optional; leaving it blank
matches every message from the selected application, or every message that
matches the optional message-type or sender filters.

S2 Watch creates an unfiltered default watch form for each supported
application: JS8Call, MagCommstat, Commstat, FLMSG, and JS8SuperSpotter.

A watch form can also apply to **All applications**. S2 Watch adds one such form
for each news category (Civil Unrest, Climate / Environment, Crime, Economy,
Health / Wellness, Infrastructure, Military Conflict, Politics, Supply Chain,
Terrorism), disabled until you enable it. Their patterns
(`s2_watch/analysis/category_patterns.py`) were built from real feeder traffic
and avoid common false matches such as "who", "Bill", "snap(shot)", or "deploy"
(an antenna). Climate / Environment combines the Weather pattern with
environmental hazards (wildfire, smoke and air quality, earthquakes, evacuations,
chemical spills). The offline analyzer uses the same vocabulary, so a message a
category form captures is classified in that category. The forms are added once;
a category form you delete is not recreated.
All watch forms start disabled when S2 Watch launches. An application is only
read while at least one of its watch forms is enabled; until then its status light
is gray and reads "Not monitored — no watch form enabled". Whenever the enabled
watch forms change, S2 Watch re-reads the whole review window in the background
with the current forms, so a form enabled later still sees the backlog; messages
already saved are shown again but not saved twice. Enable the forms you
want to monitor under **Configuration → Watch Forms…**.
The Watch Forms window provides **Import watch forms** and **Export watch forms**
buttons. Forms are exchanged as JSON files containing the application, name,
matching fields, regular-expression setting, and enabled state. Importing
updates forms with the same application and name while preserving unrelated
forms.

Matched messages and their offline newsworthiness analysis are also persisted
in SQLite, preserving the original report and classification for later review.
JS8Call's API host and port can be entered in the window; the default port is
2442, but it can be changed to match the JS8Call configuration.

An optional `OllamaNewsAnalyzer` is available for local AI analysis. It uses
Ollama at `http://127.0.0.1:11434` by default and validates the returned JSON.
`FallbackNewsAnalyzer` can use the rules engine automatically when Ollama is
unavailable or returns invalid data; no radio message is sent to a cloud
service by this integration.

The desktop window provides analysis modes for rules-only operation or local
Ollama with rules fallback, along with editable model and endpoint settings.
JS8Call host/port and analysis settings are persisted in the local SQLite
database and restored on startup.

Newsworthiness categories are standardized across rules analysis, optional
Ollama analysis, history filtering, and the user interface: Civil Unrest,
Climate / Environment, Crime, Economy, Health / Wellness, Infrastructure,
Military Conflict, Politics, Supply Chain, and Terrorism. Non-newsworthy
messages use the separate `routine` result category.

The window also includes a persisted message-history search supporting free
text, analysis category, urgency, and newsworthy-only filtering.

The Live Monitor displays matched messages in a table with time, source
application, watch form, and message columns. Its Newsworthiness table shows
time, source application, watch form, result, category, urgency, confidence,
summary, and message columns.
Displayed timestamps use `MMM-DD-YY HH:MM` format. **Configuration → Display…** provides a
choice between Local time and Zulu (UTC), and the choice applies to Live
Monitor, Newsworthiness, and History displays.
The Map tab includes persisted **Show weather radar** and **Show map filters**
toggles. When enabled, weather radar loops through RainViewer's last 2 hours of
radar (frames 10 minutes apart, pausing on the newest), and a stamp in the map's
lower-left corner shows the date and time of the frame on screen and how old it
is. The frame list refreshes every 10 minutes; RainViewer currently provides no
forecast frames. Radar needs network access and is off by default. Each frame's
images are decoded once and redrawn onto the map, because letting the embedded
browser swap radar image layers grew its memory until the map was killed.
Live Monitor rows can be sorted by clicking a column header. Double-clicking a
message opens a detail popup containing all of its displayed fields.
Double-clicking a Newsworthiness row opens a formatted analysis popup with its
classification, category, urgency, confidence, summary, source, and message.

Desktop-tray alerts are available for newsworthy messages. Alerts can be
disabled or restricted to high-urgency results, and those preferences are
persisted locally.

MagCommstat integration reads its local `traffic.db3` SQLite database without
requiring the MagCommstat window to remain visible. The adapter reads only
new rows from the `messages` table and normalizes sender, recipient, group,
rig, and message ID fields.

It also monitors MagCommstat `alerts` and `statrep` rows, exposing their table
type and structured fields to watch-form matching and analysis.

The Newsworthiness **Summary** is a one-line headline rather than a copy of the
message: the event (the matched phrase, or a named fire or storm such as "Black
Canyon Fire" or "Hurricane Lala"), the most specific location found, and key facts
from the text: casualties, acreage and containment, earthquake magnitude, heat index
and temperatures, snowfall, wind, and "until/through" times. For example, "MO -
EXCESSIVE HEAT WARNING THRU TUES HEAT INDEX 105-110f" becomes "Excessive heat warning
— Missouri · heat index 105–110°F · through Tues". Routine messages get a short gist.
Summaries written by the Ollama analyzer are kept as they are. A state code opening a
status line ("MO -", "AZ:", or "ID ..." in a StatRep) counts as that state's location.

The offline rules analyzer matches its terms at the start of a word ("flood"
matches "flooding", but "riot" does not match "patriot"). Its Climate / Environment
rules cover severe weather including heat and cold: excessive or extreme heat, heat
warnings, and dangerous or bitter cold are high urgency; heat advisories, heat index,
triple digits, record temperatures, freeze and frost products, wind chill, winter
storms, measured snowfall, icy roads, thunderstorms, hail, and similar reports are
medium. A temperature reading on its own is not treated as news.

The offline rules analyzer uses those structured fields when classifying
MagCommstat reports and produces report-specific summaries for alerts and
StatRep records.

The main window accepts the MagCommstat database path and monitors JS8Call
and MagCommstat together. Watch forms can be assigned to either application.

Commstat has a separate legacy SQLite adapter for its `traffic.db3` schema,
including its `messages`, `alerts`, and `statrep` tables.

FLMSG integration reads new `.k2s`, `.b2s`, and `.wrap` files from its
message directory, defaulting to `~/.nbems/ICS/messages`.

JS8SuperSpotter integration reads its local SQLite database, including
activity, brevity, StatRep, and store-and-forward message tables.

Settings live in the **Configuration** menu: **Analysis Mode** and **Theme**
are submenus you can switch directly, and **Watch Forms…**, **Display…**, and
**Sources…** open settings windows that can stay open while you monitor. The
window's status bar shows a one-line connection summary.

All configured source adapters initialize automatically at launch. The
**Configuration → Sources…** window shows a per-application status light: green indicates an
active connection or available local data source; red indicates disconnected,
missing, or unavailable.

The Sources window includes a review window for existing feeder data:
6 hours, 12 hours, 24 hours, 2 days, 5 days, or 1 week. The setting applies
to the initial database/file scan; messages arriving after startup continue
to be monitored normally.

The Map tab shows reporting-station locations from Maidenhead grid fields and
news locations from explicit latitude/longitude pairs or geocoded place names.
The map filters can independently show or hide station locations, news
locations, and each source application. Filter choices are saved locally.
The map works offline. Leaflet and its plugins ship with the application, and a
built-in base map (land, lakes, country/state/province borders, US counties when
zoomed in, and city names; Natural Earth and US Census data) is drawn beneath the
OpenStreetMap tiles, so it shows wherever tiles cannot load. **Use online map
tiles** in the map filters turns the online tiles off entirely. Weather radar and
place-name geocoding still need the network. `tools/build_basemap.py` regenerates
the offline map data.
Each message is placed at the most specific location it names; the first level
that yields a location wins, and every place found at that level is shown:

1. Coordinates (both numbers with decimals) and 6-character grid squares
2. City or town
3. County, parish, or borough
4. 4-character grid squares
5. State (names, "City, ST", or a MARS state subgroup such as `MR06OK`)
6. MARS region (`MR01`–`MR10`)

A state or MARS region named in the message narrows the lookup, so "Springfield"
or "Washington County" resolves to the one in that state or region, and a geocoded
town outside the named region is rejected. For StatReps, which report on the
sender's own area, the sender's grid square counts at its grid level. Matching is
case-insensitive and uses bundled lists of about 3,200 US counties and 700 larger
US cities and towns, so it works offline; smaller towns need geocoding. Links in
messages are ignored. The sender's station stays a separate marker.
Place-name geocoding is opt-in. When enabled, only extracted place names—not
complete message text—are sent to OpenStreetMap Nominatim, and results are
cached locally in SQLite. It is disabled by default and uses a short timeout;
map markers from coordinates and grids do not require geocoding.
Three optional overlays are in the map filters (each remembered, off by default),
with a legend in the map's top-right corner for whichever are on:

- **NWS watches & warnings**: active National Weather Service alerts from
  api.weather.gov, refreshed every 5 minutes (needs internet). Alerts issued for
  whole counties are drawn from the bundled county outlines. Severity uses fixed
  status colours (Minor yellow, Moderate orange, Severe red, Extreme red with a heavy
  outline) and is always named in the legend and popup; click an area for the alert
  text. Marine and offshore alerts have no county outline and are counted but not
  drawn.
- **Station coverage**: each 4-character grid square shaded by when a station there
  was last heard (under 1 hour, 1–6 hours, 6–24 hours, or silent for more than 24
  hours), from every message read, not only those a watch form matches; hover for the
  stations and message count. It shows where the network is reporting and where it
  has gone quiet.
- **Day / night (grey line)**: the night side of the Earth, recalculated every 5
  minutes; HF propagation changes along it.

Overlay shapes are kept and updated in place rather than rebuilt on each refresh,
which had grown the embedded browser's memory by about 10 MB per refresh.

The Newsworthiness label on the Map tab shows the review window being viewed, for
example "reports from the last 1 month".

The map outlines and labels the MARS regions, which follow FEMA Regions 1–10
(50 states; DC falls inside Region 3's outline). **Show MARS regions** in the map
filters turns the layer on or off, and the choice is saved. Region outlines ship
with the application, so they appear even without network access;
`tools/build_mars_regions.py` regenerates them.
MARS region codes (`MR01`–`MR10`, also written `MR1` or `@MR06`) in a message's
text or fields mark its region; a message with nothing more specific gets a marker
just below that region's label. State subgroups such as `MR06OK` (Region 6,
Oklahoma) count as that state when it belongs to the region; other suffixes (for
example `MR06MC`) count as the region. State- and region-level markers use their
own location type, **General locations (state / MARS region)**, in the map filters,
so they are never mistaken for a precise position. Region codes are not read as
Maidenhead grid squares.
Markers that overlap on screen are grouped into a numbered circle showing how many
markers it holds, coloured by the group's most common newsworthy category (neutral
when all are routine). Clicking a group zooms in to separate its markers, or fans
them out when they share one spot, such as several messages to the same MARS region.
Nearby markers that do not overlap are never grouped. Selecting a message in the
Newsworthiness table flashes its group when its marker is inside one.
Map markers use category-based icons after newsworthiness analysis. The map
includes a category legend and independent category filters; filter choices
are saved locally. A **Show map icon legend** control hides or shows the
legend while leaving all map markers visible. Routine locations use a neutral icon; newsworthy locations
use a distinct color and symbol for their assigned category. Climate /
Environment locations use a cloud-and-lightning icon. Watch Forms do not
currently include a category filter because categories are assigned after a
message passes the source-message criteria.

Reading the monitored applications, matching watch forms, newsworthiness analysis
(including Ollama), and working out map locations (including geocoding) all run on
a background thread, so a large review-window backlog or a slow analyzer does not
freeze the window; results appear in batches as they are processed. Live Monitor
keeps the newest 2,000 rows and Newsworthiness the newest 1,000 on screen; every
matched message is still saved and searchable in History. When a backlog produces
more than three newsworthy messages at once, a single summary alert is shown.

S2 Watch's icon (a radar scope with a highlighted contact) is in
`s2_watch/data/icons/`: `s2-watch.svg` for larger sizes and a simplified
`s2-watch-small.svg` used at tray and taskbar sizes (16–32 px). On Linux, a menu
entry such as `~/.local/share/applications/s2-watch.desktop` should set
`Icon=` to the SVG, `Path=` to the folder holding `s2_watch.db`, and
`StartupWMClass=s2-watch`.

One report often arrives several times: MagCommstat and JS8SuperSpotter both store
the same StatRep, JS8SuperSpotter also stores the raw JS8 frame, and other stations
relay it. S2 Watch links these copies to a single report by comparing the report
text with the radio "envelope" removed (sender, relay path, @groups, MSG, *DE*,
CommStat's CSV header, checksums), for copies up to 24 hours apart. Live Monitor
and History keep every copy; Newsworthiness, the map, and alerts show each report
once, and the **Seen** column shows how many copies arrived and through which
applications (for example "3× · MagCommstat, JS8SuperSpotter").

The **Metrics** tab counts reports, not copies, for the last 24 hours, 7 days, or
30 days: the share of newsworthy reports in each category (for example "Supply
Chain 8% · 3"), a per-category trend, shares by MARS region and state, which
application reported each report first and how many duplicate copies each added,
and the stations that sent reports first. Hover over a bar for exact numbers.

Each matched message is saved to History once, identified by its source, even
though the review window is re-read at every launch and whenever watch forms
change.

Closing the main window stops monitoring and exits S2 Watch completely. The
system-tray icon carries desktop alerts; its menu can bring the window forward
or quit. Only one copy of S2 Watch runs per user: launching it again while it
is running brings the existing window forward instead of starting another.
