"""Terminal setup wizard: walks the operator through every S2 Watch setting.

Run by the installers after installing, and any time later with ``s2-watch --configure``.
Current settings are offered as the defaults, so re-running only changes what you change.
Nothing is saved until the summary is confirmed.
"""

import json
import os
import platform
import socket
import sys
import time
import urllib.request
from pathlib import Path

from s2_watch import __version__
from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.default_forms import DEFAULT_WATCH_FORMS, install_default_forms
from s2_watch.models import ANY_APPLICATION, WatchForm
from s2_watch.storage import SQLiteStore

REVIEW_OPTIONS = (("6 hours", 6), ("12 hours", 12), ("24 hours", 24), ("2 days", 48), ("5 days", 120),
                  ("1 week", 168), ("2 weeks", 336), ("1 month", 720))
MARS_REGIONS = {
    1: "CT ME MA NH RI VT", 2: "NJ NY", 3: "DE MD PA VA WV", 4: "AL FL GA KY MS NC SC TN",
    5: "IL IN MI MN OH WI", 6: "AR LA NM OK TX", 7: "IA KS MO NE", 8: "CO MT ND SD UT WY",
    9: "AZ CA HI NV", 10: "AK ID OR WA",
}


def _home() -> Path:
    return Path.home()


def feeder_candidates() -> dict[str, list[Path]]:
    """Likely locations of each feeder's data, per operating system."""
    home = _home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    mac_support = home / "Library" / "Application Support"
    return {
        "magcommstat.database": [home / ".local/share/MagCommstat/traffic.db3", mac_support / "MagCommstat/traffic.db3",
                                 local / "MagCommstat/traffic.db3", roaming / "MagCommstat/traffic.db3"],
        "commstat.database": [home / "MyApps/Commstat/traffic.db3", home / "Commstat/traffic.db3",
                              home / ".local/share/Commstat/traffic.db3", local / "Commstat/traffic.db3",
                              Path("C:/Commstat/traffic.db3")],
        "flmsg.directory": [home / ".nbems/ICS/messages", home / "NBEMS.files/ICS/messages",
                            home / "Documents/NBEMS.files/ICS/messages"],
        "js8superspotter.database": [home / "MyApps/JS8SuperSpotter/js8spotter.db", home / "JS8SuperSpotter/js8spotter.db",
                                     local / "JS8SuperSpotter/js8spotter.db"],
    }


def search_home(filenames: set[str], seconds: float = 8.0, max_depth: int = 5) -> dict[str, list[Path]]:
    """Look for data files under the home folder (depth- and time-limited)."""
    found: dict[str, list[Path]] = {name: [] for name in filenames}
    deadline = time.monotonic() + seconds
    root = _home()
    skip = {".cache", ".git", "node_modules", ".venv", "venv", "Library", "AppData", "snap", ".mozilla", ".config"}
    for directory, subdirectories, files in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        subdirectories[:] = [d for d in subdirectories if d not in skip and not d.startswith(".") or d in (".local", ".nbems")]
        if depth >= max_depth:
            subdirectories[:] = []
        for name in filenames & set(files):
            found[name].append(Path(directory) / name)
        if time.monotonic() > deadline:
            break
    return found


class Wizard:
    def __init__(self, database: str, ask=input, say=print) -> None:
        self.database = database
        self.ask_raw = ask
        self.say = say
        self.store = SQLiteStore(database)
        install_default_forms(self.store)
        self.settings: dict[str, str] = {}
        self.forms: dict[tuple[str, str], bool] = {
            (form.application, form.name): form.enabled for form in self.store.list_watch_forms()
        }

    # -- prompts ------------------------------------------------------------------------

    def current(self, key: str, default: str = "") -> str:
        return self.settings.get(key, self.store.get_setting(key, default))

    def ask(self, prompt: str, default: str = "") -> str:
        answer = self.ask_raw(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        return answer or default

    def yes(self, prompt: str, default: bool) -> bool:
        while True:
            answer = self.ask_raw(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes", "n", "no"):
                return answer.startswith("y")
            self.say("  Please answer y or n.")

    def choose(self, prompt: str, options: list[tuple[str, str]], current: str) -> str:
        for index, (label, _value) in enumerate(options, start=1):
            marker = " (current)" if _value == current else ""
            self.say(f"  {index}. {label}{marker}")
        default = next((str(index) for index, (_l, value) in enumerate(options, start=1) if value == current), "1")
        while True:
            answer = self.ask(prompt, default)
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][1]
            self.say(f"  Please enter a number from 1 to {len(options)}.")

    def heading(self, title: str) -> None:
        self.say("")
        self.say(f"== {title} " + "=" * max(0, 60 - len(title)))

    # -- steps --------------------------------------------------------------------------

    def run(self) -> bool:
        self.say(f"S2 Watch {__version__} setup")
        self.say(f"Settings are saved in: {self.database}")
        self.say("Press Enter to accept the value in [brackets].")
        self.step_sources()
        self.step_review_window()
        self.step_focus()
        self.step_watch_forms()
        self.step_analysis()
        self.step_alerts()
        self.step_map()
        self.step_display()
        return self.step_summary()

    def step_sources(self) -> None:
        self.heading("1. Radio applications (feeders)")
        self.say("S2 Watch reads messages from these applications. Answer n for any you do not use.")
        if self.yes("Do you use JS8Call?", bool(self.current("js8call.host", "127.0.0.1"))):
            host = self.ask("  JS8Call TCP API host", self.current("js8call.host", "127.0.0.1"))
            while True:
                port = self.ask("  JS8Call TCP API port", self.current("js8call.port", "2442"))
                if port.isdigit() and 1 <= int(port) <= 65535:
                    break
                self.say("  The port must be a number from 1 to 65535.")
            try:
                socket.create_connection((host, int(port)), timeout=2).close()
                self.say("  OK: JS8Call is answering on that address.")
            except OSError:
                self.say("  Note: nothing answered there right now. That's fine if JS8Call isn't running;"
                         " enable its TCP API (File > Settings > Reporting) before starting S2 Watch.")
            self.settings.update({"js8call.host": host, "js8call.port": port})
        else:
            self.settings.update({"js8call.host": "", "js8call.port": self.current("js8call.port", "2442")})

        candidates = feeder_candidates()
        searched: dict[str, list[Path]] | None = None
        for key, label, filename, is_directory in (
            ("magcommstat.database", "MagCommstat", "traffic.db3", False),
            ("commstat.database", "CommStat", "traffic.db3", False),
            ("flmsg.directory", "FLMSG (NBEMS)", "", True),
            ("js8superspotter.database", "JS8SuperSpotter", "js8spotter.db", False),
        ):
            existing = self.current(key)
            found = [str(path) for path in candidates[key] if (path.is_dir() if is_directory else path.is_file())]
            suggestion = existing if existing and Path(existing).exists() else (found[0] if found else "")
            if not suggestion and filename and self.yes(f"Search your home folder for {label}'s {filename}?", True):
                if searched is None:
                    self.say("  Searching (up to a few seconds)...")
                    searched = search_home({"traffic.db3", "js8spotter.db"})
                matches = [str(path) for path in searched.get(filename, [])]
                if matches:
                    self.say("  Found: " + "; ".join(matches[:4]))
                    suggestion = matches[0]
            if not self.yes(f"Do you use {label}?", bool(suggestion or existing)):
                self.settings[key] = ""
                continue
            what = "message folder" if is_directory else f"database file ({filename})"
            while True:
                path = self.ask(f"  {label} {what}", suggestion or existing)
                path = os.path.expanduser(path)
                exists = Path(path).is_dir() if is_directory else Path(path).is_file()
                if exists:
                    self.say("  OK: found.")
                    break
                if path and self.yes(f"  {path} does not exist yet. Use it anyway?", False):
                    break
                if not path:
                    break
            self.settings[key] = path

    def step_review_window(self) -> None:
        self.heading("2. Review window")
        self.say("How far back S2 Watch reads existing messages when it starts (and after watch-form changes).")
        options = [(label, str(hours)) for label, hours in REVIEW_OPTIONS]
        self.settings["review.hours"] = self.choose("Choose", options, self.current("review.hours", "24"))

    def step_focus(self) -> None:
        self.heading("3. Focus regions")
        self.say("Reports from your MARS regions (= FEMA regions) are listed first, alert normally and are")
        self.say("emphasised on the map; the rest of the U.S. is secondary. Leave blank for the whole U.S.")
        for region, states in MARS_REGIONS.items():
            self.say(f"  Region {region:>2}: {states}")
        current = self.current("focus.regions", "")
        while True:
            answer = self.ask("Your regions (numbers or state codes, e.g. '8' or 'CO WY'; '-' for none)", current or "-")
            regions = self.parse_regions(answer)
            if regions is not None:
                break
            self.say("  Please enter region numbers 1-10 and/or two-letter state codes, separated by spaces or commas.")
        self.settings["focus.regions"] = ",".join(str(region) for region in sorted(regions))

    @staticmethod
    def parse_regions(answer: str) -> set[int] | None:
        if answer.strip() in ("", "-"):
            return set()
        state_region = {state: region for region, states in MARS_REGIONS.items() for state in states.split()}
        regions = set()
        for token in answer.replace(",", " ").upper().split():
            token = token.removeprefix("MR").lstrip("0") or "0"
            if token.isdigit() and 1 <= int(token) <= 10:
                regions.add(int(token))
            elif token in state_region:
                regions.add(state_region[token])
            else:
                return None
        return regions

    def step_watch_forms(self) -> None:
        self.heading("4. What to watch for")
        self.say("Category watch forms pick out newsworthy reports. Choose the categories to watch.")
        all_on = self.yes("Watch all ten categories (recommended)?",
                          all(self.forms.get((ANY_APPLICATION, c), False) for c in NEWS_CATEGORIES)
                          or not any(self.forms.get((ANY_APPLICATION, c), False) for c in NEWS_CATEGORIES))
        for category in NEWS_CATEGORIES:
            enabled = all_on or self.yes(f"  Watch {category}?", self.forms.get((ANY_APPLICATION, category), False))
            self.forms[(ANY_APPLICATION, category)] = enabled
        self.forms[("OSINT", DEFAULT_WATCH_FORMS["OSINT"])] = self.yes(
            "Include OSINT (free official/open sources: FAA security restrictions, FEMA, USGS, SPC storm reports,\n"
            "  NIFC wildfires, State Dept advisories, news on unrest) as reports? Needs internet.",
            self.forms.get(("OSINT", DEFAULT_WATCH_FORMS["OSINT"]), True))
        # The per-application "every message" forms stay disabled; enable them in the app if wanted.
        self.say("Per-application forms (every message from one application) are left disabled;\n"
                 "  enable them later from Configuration → Watch Forms if you want them.")

    def step_analysis(self) -> None:
        self.heading("5. Analysis")
        self.say("The built-in rules classify reports offline. A local Ollama model can be used instead,")
        self.say("with the rules as a fallback (Ollama must be installed separately: https://ollama.com).")
        mode = self.choose("Choose", [("Rules only (offline, fast)", "rules"),
                                      ("Local Ollama with rules fallback", "ollama")], self.current("analysis.mode", "rules"))
        self.settings["analysis.mode"] = mode
        if mode == "ollama":
            endpoint = self.ask("  Ollama endpoint", self.current("ollama.endpoint", "http://127.0.0.1:11434"))
            model = self.ask("  Ollama model", self.current("ollama.model", "llama3.2"))
            try:
                with urllib.request.urlopen(endpoint.rstrip("/") + "/api/tags", timeout=3) as response:
                    models = [entry.get("name", "") for entry in json.loads(response.read()).get("models", [])]
                self.say("  OK: Ollama is answering." + (
                    "" if any(name.split(":")[0] == model.split(":")[0] for name in models)
                    else f" Model '{model}' is not installed yet; run: ollama pull {model}"))
            except (OSError, ValueError):
                self.say("  Note: Ollama did not answer; S2 Watch will use the rules until it does.")
            self.settings.update({"ollama.endpoint": endpoint, "ollama.model": model})

    def step_alerts(self) -> None:
        self.heading("6. Desktop alerts")
        enabled = self.yes("Show desktop alerts for newsworthy reports?", self.current("alerts.enabled", "1") == "1")
        high = enabled and self.yes("  Only for high-urgency reports?", self.current("alerts.high_urgency_only", "0") == "1")
        self.settings.update({"alerts.enabled": "1" if enabled else "0", "alerts.high_urgency_only": "1" if high else "0"})

    def step_map(self) -> None:
        self.heading("7. Map and network features")
        self.say("The map works offline. These optional features use the internet:")
        for key, question, default in (
            ("map.tiles.online", "Use online OpenStreetMap tiles when available?", "1"),
            ("map.nws.visible", "Show NWS watches & warnings on the map?", "1"),
            ("map.aircraft.visible", "Show aircraft (ADS-B: civilian, military, medevac, emergencies)?", "0"),
            ("map.radar.visible", "Show weather radar (last 2 hours)?", "0"),
            ("map.coverage.visible", "Show station coverage (when each grid square was last heard)?", "0"),
            ("map.greyline.visible", "Show day/night (grey line)?", "0"),
        ):
            self.settings[key] = "1" if self.yes(question, self.current(key, default) == "1") else "0"
        self.say("Place-name geocoding sends place names (never whole messages) to OpenStreetMap to locate small towns.")
        self.settings["geocoding.enabled"] = "1" if self.yes("Enable geocoding?", self.current("geocoding.enabled", "0") == "1") else "0"

    def step_display(self) -> None:
        self.heading("8. Display")
        self.settings["display.time_zone"] = self.choose(
            "Times shown in", [("Local time", "local"), ("Zulu (UTC)", "utc")], self.current("display.time_zone", "local"))
        self.settings["ui.theme"] = self.choose(
            "Theme", [("Light", "light"), ("Dark", "dark")], self.current("ui.theme", "light"))

    def step_summary(self) -> bool:
        self.heading("Summary")
        labels = {"js8call.host": "JS8Call host", "js8call.port": "JS8Call port", "magcommstat.database": "MagCommstat",
                  "commstat.database": "CommStat", "flmsg.directory": "FLMSG", "js8superspotter.database": "JS8SuperSpotter",
                  "review.hours": "Review window (hours)", "focus.regions": "Focus regions", "analysis.mode": "Analysis",
                  "alerts.enabled": "Desktop alerts", "alerts.high_urgency_only": "High urgency only",
                  "display.time_zone": "Time zone", "ui.theme": "Theme"}
        for key, label in labels.items():
            if key not in self.settings:
                continue
            value = self.settings[key]
            if key.startswith("alerts."):
                value = "yes" if value == "1" else "no"
            elif not value:
                value = "whole U.S." if key == "focus.regions" else "(not used)"
            self.say(f"  {label:24} {value}")
        watched = [category for category in NEWS_CATEGORIES if self.forms.get((ANY_APPLICATION, category))]
        self.say(f"  {'Watching':24} {', '.join(watched) or 'nothing (enable watch forms in the app)'}")
        self.say(f"  {'OSINT reports':24} {'yes' if self.forms.get(('OSINT', DEFAULT_WATCH_FORMS['OSINT'])) else 'no'}")
        features = [key.split('.')[1] for key in ("map.tiles.online", "map.nws.visible", "map.aircraft.visible",
                    "map.radar.visible", "map.coverage.visible", "map.greyline.visible", "geocoding.enabled")
                    if self.settings.get(key) == "1"]
        self.say(f"  {'Network features':24} {', '.join(features) or 'none (fully offline)'}")
        if not self.yes("Save these settings?", True):
            self.say("Nothing was changed.")
            return False
        for key, value in self.settings.items():
            self.store.set_setting(key, value)
        for form in self.store.list_watch_forms():
            wanted = self.forms.get((form.application, form.name))
            if wanted is not None and wanted != form.enabled:
                self.store.save_watch_form(WatchForm(form.application, form.name, form.match_text, wanted,
                                                     form.use_regex, form.message_type, form.sender))
        self.say("Saved. You can change any of this later in the app (Configuration menu, Map filters),")
        self.say("or run the wizard again with:  s2-watch --configure")
        return True


def run_wizard(database: str, ask=input, say=print) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # e.g. a Windows console that cannot show every character
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = Wizard(str(Path(database).expanduser()), ask, say).run()
    except (KeyboardInterrupt, EOFError):
        say("\nSetup cancelled; nothing was changed.")
        return 1
    return 0 if saved else 1


def default_database() -> Path:
    """The per-user data location the installers use."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "S2Watch" / "data" / "s2_watch.db"
    if system == "Darwin":
        return Path.home() / "Library/Application Support/S2 Watch/s2_watch.db"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "s2-watch" / "s2_watch.db"


if __name__ == "__main__":
    raise SystemExit(run_wizard(sys.argv[1] if len(sys.argv) > 1 else str(default_database())))
