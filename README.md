# S2 Watch

S2 Watch is a cross-platform desktop monitor for messages from JS8Call and
future application adapters.

S2 Watch was created by N1ROG. Use **Help → About S2 Watch** to view the
application version and a short description.

The primary adapter connects to JS8Call's local TCP API, so the source
application can remain minimized while S2 Watch displays matching messages.
The text-log adapter remains available as a fallback for installations where
the TCP API is not enabled.

## Installing

**To install S2 Watch, see [INSTALL.md](INSTALL.md)**: clone this repository and run
`installer/install.sh` (or `installer\install.cmd`), then update with `git pull`; or run
the installer package from the [latest release](https://github.com/n1rog/S2_Watch/releases/latest).

Installed from a clone, S2 Watch runs the clone's code (an editable install), so `git pull`
updates it. When a pull changes `pyproject.toml`'s dependencies, S2 Watch installs them at
its next start, before Qt loads (`s2_watch/checkout.py`).

Install scripts for Linux, macOS and Windows live in `installer/`. Each one checks
for and downloads what S2 Watch needs (Python 3.11+, Qt's system libraries on Linux,
the Visual C++ runtime on Windows, and PySide6), installs S2 Watch into its own
Python environment, creates menu and desktop shortcuts, and then runs the setup
wizard. The wizard walks through sources, the review window, focus regions, watch
forms, analysis, alerts, map layers and display.

Build the installer package (it carries the program with it, so installing needs no
access to this repository):

```bash
python tools/make_installer_package.py   # -> dist/S2Watch-<version>-installer.zip
```

Unzip it, then run `bash install.sh` (Linux/macOS) or double-click `install.cmd`
(Windows). See `installer/README-INSTALL.txt`. Rerun the setup wizard any time with
`s2-watch --configure`. `--database PATH` selects the data file.

## Your data and backups

Settings, watch forms, reports, labels and history live in one SQLite file
(`s2_watch.db`; its location is shown by the installer). Once a day S2 Watch copies
it to a `backups` folder beside it and keeps the last 14 copies. Use
**Configuration → Back Up Now** for an extra copy and **Open Backups Folder** to
find them. To restore, quit S2 Watch and copy a backup over `s2_watch.db`.

At launch the Newsworthiness table and map are rebuilt from the saved reports in
the watch period, so reports, labels and notes don't depend on a feeder still
having the original message.

## Events

The **Events** tab groups reports — radio and official — that describe one real-world
happening: a StatRep, a JS8Call message, an NWS warning and a FEMA declaration about one
flood are one event. Reports join an event when they are the same category and kind of
hazard, within 48 hours of its latest report and about 100 km (150 km for earthquakes),
or in the same state when only the state is known. A named official incident (a NIFC
fire, a USGS quake, an FAA restriction) is one event however long it lasts. Stories
without a physical hazard (crime, politics...) also need distinctive wording in common.
Each event shows its sources, official confirmation and how far ahead radio reported it.

Select an event to see what it is about. The panel beside the list (on the map page and
on the Events tab) shows its category, area, urgency and when it was heard, whether an
official source confirms it and radio's head start, your labels and notes, then every
report in the order heard, with the full text, the sender, its own confirmation and a link
for official and news items. The buttons under it show the event on the map, draft a
follow-up, label it (or edit its note), list its reports, or close it.

**Events that are over.** A radio or official report saying it's over closes its event:
"all clear", "power restored", "warning cancelled", "evacuation order lifted", "bridge
reopened", "100% contained", "suspect in custody", "net secured" (not when negated: "power
not yet restored"). News headlines don't close events. You can close an event yourself
(**Close event…**, with an optional reason) or reopen it; a later report reopens a closed
event. An event with no reports for 12 hours shows as **Quiet** (change the hours under
**Configuration → Display…**). The **Status** column, next to Last activity, shows Active,
Quiet or Closed; only radio and official reports close events, and all-clear reports
saved before events could close are applied once on the first start of 0.28; closed events are greyed, the Events tab can hide them, and
briefings list them under **Resolved in the Period** instead of as current activity.

**Single source.** An event radio reported, but only one station has (copies and relays
count once, the same call sign twice is one station), with no official confirmation after
2 hours, is flagged **Single source** in the event panel and in briefings, whose
intelligence gaps then ask whether another station or an official source can confirm it.
News stories alone aren't flagged; most are one article.

**Where a report is.** The event panel shows where each report was placed, with a **fix
location** link: type where it really is (Town, ST · a county · coordinates · a grid
square). Towns S2 Watch doesn't list can be looked up on OpenStreetMap, only when you
press the button. Tick **Remember** and every message using the same words ("BIXBY")
is placed there from then on; otherwise the correction applies to that message only.

**Time in message.** A radio report's own time ("AT 1420Z", "152330Z", "9:15 AM CDT", a
MagCommstat or MAGNET date-time stamp) is shown beside when it was heard, and follow-up
timelines use it. Forecast wording ("until 1800Z", "expected at") and times later than
the message was heard don't count. Radio lead still compares when reports were heard.

## Keyword noise

**Reports → Keyword Noise** lists the keywords that flagged newsworthy reports you then
marked Ignored, noisiest first, with how many came from radio and how many from feeds.
**Stop using this word** and it no longer makes a report news in that category (other
words in the same message still can), for every source; **Use again** undoes it. The
watch period is then checked again with the new words, as it is after adding a keyword
from Possible Misses.

## Exercise traffic

Clear exercise markers ("EXERCISE EXERCISE EXERCISE", "THIS IS A DRILL", "exercise
message", "for exercise purposes only", "simulated emergency", STARTEX/ENDEX, a message
that starts "EXERCISE:") mark radio traffic as exercise traffic; "real world" or "no duff"
marks it real. Ordinary words don't count ("Exercise Normal Precautions", "military
drills"), and official and news feeds are never exercise traffic. Test traffic ("THIS IS
A TEST", "test message") is never news.

Exercise traffic is kept off the news lists, events and briefings. During an exercise,
turn on **Configuration → Exercise in Progress** (a red EXERCISE marker shows by the
clock): exercise traffic is then handled like real news, marked EXERCISE in the lists, the
event panel, follow-up reports and briefings (subject prefixed, and EXERCISE EXERCISE
EXERCISE at the top and bottom). Possible Misses skips exercise and test traffic.

## Sender track records

**Reports → Sender Track Records** shows how each radio station's newsworthy reports have
held up: confirmed by an official source, or marked Of interest / Follow-up, counts for the
station; marked Ignored counts against it. With 5 or more judged reports, the share that
held up suggests a reliability grade: B usually reliable (85%+), C fairly reliable (65%+),
D not usually reliable (40%+), E unreliable; fewer is F, cannot be judged. A is left to
official sources. MAGNET reports use the grade in each station's source rating (e.g.
[B2]), and the event panel shows it beside each report. Exercise and test traffic doesn't
count.

Under the map, **Show: Events | Reports** switches the Newsworthiness list between events
(the default) and individual reports. Right-click events to label all their reports,
edit a note, draft a follow-up (from the newest radio report), show them on the map, or
list their reports; the I/F/X-style shortcuts work there too. Clicking a map marker
selects its event; new and updated events are highlighted briefly; the row limit applies,
with events labelled Of interest or Follow-up always shown.

## MAGNET S2 reports and briefings

Follow-up reports (right-click a report or event → **Draft follow-up report…**) and shift
briefings (**Draft briefing…** on the Map tab for the watch period, on the Events tab for
its period, or **Reports → Draft Shift Briefing…**) are drafted as **MAGNET S2 Intelligence
Reports**: the routing table (Subject, Purpose, DTG, Reporting Period, Geographic Focus,
Precedence, MagCon Status, Sources), then Summary (BLUF), Background, Situation with a
timeline, Comments / Assessment with a probability table, Intelligence Gaps, Mitigation
Recommendations, MAGNET Guidance and the Source List with suggested Admiralty ratings.

S2 Watch fills what its data supports (routing, BLUF, Situation, timeline, gaps, sources,
up to 15 WordPress tags); the analysis sections start as prompts in parentheses. With
Ollama, **Suggest assessment & gaps** drafts the assessment and adds gaps for the analyst
to check. Edit the text, then **Save Word report…** (the MAGNET .docx format) or **Save web
page…** (the MAGNET HTML template), named `MAGNET_S2_TOPIC_YYMMDD-HHMMZ`; the FLMSG and
JS8Call versions are made from the same text. The MagCon status used is remembered for the
next report.

**Save in S2 Watch** keeps a draft inside S2 Watch at any point (for example after Ollama's
suggestions); saving again updates it, closing with unsaved changes asks first, and marking a
briefing sent or a report followed up saves it too. **Reports → Saved Reports & Briefings…**
lists them to reopen (with every save and export option) or delete.

## Possible misses (Ollama)

Watch forms keep only messages with news keywords. In Ollama mode, S2 Watch also reads
the radio traffic they didn't keep — slowly, in the background, newest first, each message
once — and lists what the model thinks is news in the **Possible Misses** tab. It skips
heartbeats, CQs, signal reports and messages JS8Call marks as fragments, uses the computer
at most a third of the time and waits while it is busy. Turn it off under Configuration →
Analysis Mode.

Triage (buttons, right-click, several rows at once; double-click for the full message):

- **Add as report…** — correct the headline, category, urgency and place first. Optionally
  add a keyword (the model's matched word is suggested; the form shows how much recent
  radio traffic it would match): it is added to the category's watch form and to the
  keyword rules, so the next message with it is kept and flagged straight away.
- **Dismiss** (not news) hides it; **Show dismissed** to review or **Restore**.
- **Delete** removes it for good (it is not checked again).

Finds older than the watch period leave the list. Your latest decisions are given to the
model as examples, so it repeats fewer of the same mistakes.

## Aircraft (ADS-B)

All U.S. traffic is fetched at every zoom level from the free adsb.lol and
adsb.fi services: the whole country every 3 minutes, focus regions every minute
and a zoomed-in view every 30 seconds. This uses about 3–4 GB of internet data a
day while the aircraft layer is on. Medevac aircraft are recognised by
air-ambulance callsigns and by registration to air-medical operators
(`s2_watch/data/air_ambulance.json`; refresh it with
`python tools/build_air_ambulance.py`).

## Continuous integration and releases

`.github/workflows/ci.yml` runs the tests, a Linux install from the installer package,
and a Linux install from a git clone on every push. The clone install checks that S2
Watch runs from the clone and that a pulled change to the required packages is
installed at the next start. Pushing a version tag (`git push origin v1.2.3`) runs all
of it on Linux, Windows and macOS and then publishes a GitHub release with
`S2Watch-<version>-installer.zip` attached. Push one tag at a time: GitHub starts no
workflows when more than three tags are pushed at once. The full run can also be
started from the Actions tab (CI → Run workflow).

## Long-run check

`python tools/soak_monitor.py --hours 12` watches a running S2 Watch (Linux) and
reports whether its memory keeps growing, following it across restarts.

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

**Configuration → Watch Forms** lists the forms in groups: **Topics** (the keyword
forms for each news category, checked in every application's messages), **Whole
applications** (every message from one application), and **Your forms**. Each form says
what it matches in plain words (e.g. "Keywords: shooting, shooter, shots fired…"), with
the full pattern in its tooltip. Check or uncheck a form to turn it on or off;
double-click to edit it. A form keeps every message, messages containing some text,
messages with any of a list of words (whole words: "ice" doesn't match "police"), or
messages matching a regular expression, optionally only of one message type or from
one sender.

The same page has an on/off switch for each **OSINT source** (SPC storm reports, USGS
earthquakes, NIFC wildfires, FEMA declarations, FAA security TFRs, State Department
advisories, GDELT news), with how often each is fetched, when it last updated, and any
error. The same switches are in the **Configuration → Watch Forms** menu, under
**Edit Watch Forms…**. Turning a source off stops fetching it and removes its items from the map and
from official confirmations; reports already saved stay until they leave the watch
period.

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
S2 Watch remembers its settings between launches: which watch forms are enabled,
map filters and overlays, focus regions, theme, analysis and alert options, the window
size and position, the tab you were on, Newsworthiness column widths, and the Metrics
period and scope. An application is only
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

- **Aircraft (ADS-B)**: free community ADS-B data from adsb.lol (adsb.fi as a
  fallback). Military aircraft and emergency squawks (7500 hijack, 7600 radio failure,
  7700 emergency) show everywhere; all traffic, including general aviation, helicopters
  and air ambulances, shows inside your focus regions and wherever you zoom in to
  regional scale (the free APIs limit each query to 250 nautical miles, so the whole
  U.S. at once is not practical). Helicopters and light aircraft have their own
  symbols. Colours: civilian yellow, military blue, medevac / air ambulance red
  (recognised by callsign: LIFE…, MEDEVAC, AIRLIFE, CAREFLT, REACH …; medical flights
  flying under their tail number show as civilian). An emergency squawk adds a black
  ring and a black label (e.g. "7700 Emergency") around the aircraft, in its usual
  colour. Zoomed out, civilian traffic draws as dots. Aircraft
  are shown on the map only; they raise no alerts. Requests are spaced out and back
  off when the services rate-limit.

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

Digests such as weekly briefings, bulletins and news roundups ("MAGNET WEEKLY
BRIEFING", "EmComm DocDrop News") are split into their items, each line or section that
starts with a state ("MN — …", "MI/WI - …"), a heading ("CIVIL UNREST: …") or a bullet.
Each item becomes its own report with its own category, location, summary and Official
check, and shows which digest it came from ("— from “MAGNET WEEKLY BRIEFING …” (item 3
of 22)"). The whole digest is kept in Live Monitor and History with the watch form
"Digest". Section headings are kept apart from the item text, so "DOMESTIC TERRORISM: No
verified plot" is not read as terrorism. When several watch forms match a report, the
one named after its category gets the credit.

One report often arrives several times: MagCommstat and JS8SuperSpotter both store
the same StatRep, JS8SuperSpotter also stores the raw JS8 frame, and other stations
relay it. S2 Watch links these copies to a single report by comparing the report
text with the radio "envelope" removed (sender, relay path, @groups, MSG, *DE*,
CommStat's CSV header, checksums), for copies up to 24 hours apart. Live Monitor
and History keep every copy; Newsworthiness, the map, and alerts show each report
once, and the **Seen** column shows how many copies arrived and through which
applications (for example "3× · MagCommstat, JS8SuperSpotter").

**Focus regions** (Configuration → Focus Regions) let you concentrate on your own MARS
regions, with the rest of the U.S. secondary; with none chosen, the whole U.S. is
treated the same. With focus regions set, Newsworthiness lists reports from them
first (newest first), then the rest of the U.S., and a **Region** column shows each
report's MARS region and state; tray alerts for reports outside the focus regions are
limited to high urgency; the map opens on the focus regions, drawn bold with the other
regions faded; aircraft traffic is shown in full inside them; and Metrics offers a
Scope of "My focus regions" or "All of the U.S.". Reports with no known location count
as secondary. The window title shows the focus, e.g. "S2 Watch 0.3.0 — focus R8".

**OSINT** is a sixth source alongside the radio applications, with its own status
light and an "All OSINT reports" watch form. It collects free, keyless open and
official sources on a background thread: US news about protests, riots, unrest,
shootings, bombings and terrorism from the GDELT news search (news-derived, not
official; every 15 minutes), FAA security flight restrictions (mostly VIP movements
and security events; every 30 minutes), State Department travel advisories whose
level or risks changed in the last 30 days, FEMA disaster and emergency declarations
(hourly), USGS earthquakes (every 10 minutes), SPC tornado, wind and hail reports
(every 15 minutes), and NIFC wildfires (hourly). Only notable earthquakes (US M4.0+,
elsewhere M6+) and wildfires (10,000+ acres, or discovered in the last 3 days, not yet
contained, prescribed burns excluded) become reports; smaller ones are still used to
confirm radio reports. Items flow through watch forms, the analyzer, deduplication,
locations, labels and Metrics like radio reports. DHS NTAS, CISA and U.S. Embassy
alert feeds are not included because their servers refuse automated clients.

The **Official** column in Newsworthiness marks a radio report that an official source
confirms: same kind of hazard, same place, around the same time. Sources and rules:
an active NWS alert covering the report's location (inside its outline or one of its
counties, active around the report's time); a USGS earthquake within 150 km and 24
hours; an SPC storm report within 50 km and 12 hours; an NIFC wildfire within 60 km;
a FAA security flight restriction within 50 km and 7 days (for security/VIP reports);
and a FEMA declaration for the same hazard in the report's state within 30 days. The
strongest match is shown with a count of any others, for example "✓ NWS Flood Watch ·
radio 25 min earlier +1" when the radio report came first. Apart from FEMA, reports
need a specific location (coordinates, grid square, city or county). NWS alerts are
fetched every 5 minutes for this even when the NWS map layer is off; OSINT items are
never checked against each other.

Reports in Newsworthiness can be labelled for triage. Select one or more rows
(Ctrl- or Shift-click), then right-click → **Mark as**, or press a key:
**I** ★ Of interest, **F** ↪ Follow-up created (asks for an optional note, such as
your follow-up report number), **X** ⊘ Ignored, **C** clear the label, **N** edit the
note. Each report has one status, shown in the **Status** column, kept across restarts,
and applied to later copies of the same report. Ignored reports stay visible but are
greyed out, their map markers are faded, they raise no alerts, and Metrics leaves
them out (the Newsworthy reports tile says how many were ignored).

The **Metrics** tab counts reports, not copies, for the last 24 hours, 7 days, or
30 days: the share of newsworthy reports in each category (for example "Supply
Chain 8% · 3"), a per-category trend, shares by MARS region and state, which
application reported each report first and how many duplicate copies each added,
and the stations that sent reports first. Hover over a bar for exact numbers.

Each matched message is saved to History once, identified by its source, even
though the review window is re-read at every launch and whenever watch forms
change.

The version is set once, as `__version__` in `s2_watch/__init__.py`, and shown in the
window title and **Help → About**. S2 Watch uses semantic versioning: 0.x.0 adds
features, 0.x.y only fixes things. Each release is tagged in git (`v0.2.0`).

Closing the main window stops monitoring and exits S2 Watch completely. The
system-tray icon carries desktop alerts; its menu can bring the window forward
or quit. Only one copy of S2 Watch runs per user: launching it again while it
is running brings the existing window forward instead of starting another.
