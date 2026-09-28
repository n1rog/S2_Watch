from pathlib import Path

from s2_watch.models import ANY_APPLICATION
from s2_watch.setup_wizard import Wizard, run_wizard
from s2_watch.storage import SQLiteStore


def scripted(answers):
    queue = list(answers)

    def ask(prompt):
        assert queue, f"wizard asked more than scripted: {prompt}"
        return queue.pop(0)

    return ask, queue


def test_wizard_walks_every_step_and_saves(tmp_path: Path, monkeypatch) -> None:
    flmsg = tmp_path / "messages"
    flmsg.mkdir()
    monkeypatch.setattr("s2_watch.setup_wizard.feeder_candidates", lambda: {
        "magcommstat.database": [], "commstat.database": [], "flmsg.directory": [flmsg], "js8superspotter.database": []})
    monkeypatch.setattr("s2_watch.setup_wizard.search_home", lambda names: {name: [] for name in names})
    ask, queue = scripted([
        "y", "127.0.0.1", "2442",        # JS8Call used, host, port (connection may fail: just a note)
        "n", "n",                         # MagCommstat: search? no; used? no
        "n", "n",                         # CommStat: search? no; used? no
        "", "",                           # FLMSG found -> used (default yes), path accepted
        "n", "n",                         # JS8SuperSpotter: search? no; used? no
        "6",                              # review window: 1 week
        "co ok",                          # focus regions by state -> 8 and 6
        "", "y",                          # all categories, OSINT yes
        "1",                              # rules
        "y", "n",                         # alerts on, not only high urgency
        "", "", "y", "", "", "",          # map: tiles, NWS, aircraft yes, radar/coverage/greyline defaults
        "n",                              # geocoding no
        "2", "2",                         # UTC, dark
        "y",                              # save
    ])
    output = []
    assert run_wizard(str(tmp_path / "data" / "s2.db"), ask, output.append) == 0
    assert queue == [], "not all scripted answers were used"
    store = SQLiteStore(tmp_path / "data" / "s2.db")
    assert store.get_setting("flmsg.directory") == str(flmsg)
    assert store.get_setting("magcommstat.database") == ""
    assert store.get_setting("review.hours") == "168"
    assert store.get_setting("focus.regions") == "6,8"
    assert store.get_setting("map.aircraft.visible") == "1" and store.get_setting("map.radar.visible") == "0"
    assert store.get_setting("display.time_zone") == "utc" and store.get_setting("ui.theme") == "dark"
    enabled = {(form.application, form.name) for form in store.list_watch_forms() if form.enabled}
    assert (ANY_APPLICATION, "Crime") in enabled and ("OSINT", "All OSINT reports") in enabled
    assert ("JS8Call", "All JS8Call messages") not in enabled


def test_nothing_is_saved_when_the_summary_is_declined(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("s2_watch.setup_wizard.feeder_candidates", lambda: {
        "magcommstat.database": [], "commstat.database": [], "flmsg.directory": [], "js8superspotter.database": []})
    answers = ["n"] + ["n", "n"] * 2 + ["n"] + ["n", "n"] + ["1", "-", "", "n", "1", "n"] + ["n"] * 7 + ["1", "1", "n"]
    ask, _queue = scripted(answers)
    assert run_wizard(str(tmp_path / "s2.db"), ask, lambda *_: None) == 1
    assert SQLiteStore(tmp_path / "s2.db").get_setting("review.hours", "unset") == "unset"


def test_region_answers() -> None:
    assert Wizard.parse_regions("8") == {8}
    assert Wizard.parse_regions("MR06, co WY") == {6, 8}
    assert Wizard.parse_regions("-") == set()
    assert Wizard.parse_regions("12") is None and Wizard.parse_regions("XX") is None
