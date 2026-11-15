"""Sports stories use violent words for play; they are not crime or terrorism."""

import pytest

from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.models import Message

ANALYZER = RuleBasedNewsAnalyzer()


@pytest.mark.parametrize("headline", [
    "Prolific Shooting Performance Helps Aggies to 120-82 Win over UC San Diego",
    "Wildcats shoot 55% in 78-60 win over Bears; Smith scores 24 points",
    "Explosive offense powers Chiefs past Raiders 35-10 in season opener",
    "Cold-Shooting Rams Fall to Cowgirls in women's basketball opener",
])
def test_play_is_not_violence(headline: str) -> None:
    assert not ANALYZER.analyze(Message("OSINT", headline)).newsworthy


@pytest.mark.parametrize("headline", [
    "Shooting at high school basketball game leaves 2 wounded, police say",
    "3 charged in deadly shooting at youth football championship game",
    "Shooting in San Diego leaves one dead",
    "Bomb threat forces evacuation of football stadium",
])
def test_real_violence_at_a_game_is_still_news(headline: str) -> None:
    assert ANALYZER.analyze(Message("OSINT", headline)).newsworthy


def test_a_sports_outlet_is_enough() -> None:
    for outlet in ("Utah State University Athletics", "ESPN", "Georgetown Hoyas | Georgetown University Athletics"):
        message = Message("OSINT", "Hot-Shooting Hoyas Down Peacocks, 97-37", fields={"outlet": outlet, "type": "NEWS"})
        assert not ANALYZER.analyze(message).newsworthy, outlet
    shot = Message("OSINT", "Shooting near stadium after game leaves student wounded, police say",
                   fields={"outlet": "ESPN"})
    assert ANALYZER.analyze(shot).newsworthy                     # real violence, whoever reports it


def test_radio_traffic_is_left_alone() -> None:
    # The check is for news stories: a radio operator's report is taken as written.
    message = Message("JS8Call", "SHOTS FIRED NEAR STADIUM AFTER FOOTBALL GAME 21-14 WIN OVER RIVALS")
    assert ANALYZER.analyze(message).newsworthy


def test_stored_sports_stories_are_corrected_once(tmp_path) -> None:
    from datetime import datetime, timezone

    from s2_watch.analysis.base import AnalysisResult
    from s2_watch.analysis.rules import sports_play
    from s2_watch.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "s2.db")
    crime = AnalysisResult(True, "Crime", "high", "Shooting — San Diego, CA", 0.9, ("shooting",))
    ids = {}
    for text, outlet in (("Prolific Shooting Performance Helps Aggies to 120-82 Win over UC San Diego",
                          "Utah State University Athletics"), ("Shooting in San Diego leaves one dead", "NBC 7")):
        message = Message("OSINT", text, received_at=datetime.now(timezone.utc), fields={"outlet": outlet})
        message_id = store.save_message(message, text)
        store.save_analysis(message_id, crime, "ollama:llama3.2")
        ids[text[:8]] = store.assign_report(message_id, message, crime).report_id
    changed = store.make_routine(lambda m: sports_play(m.raw_content, m.fields.get("outlet", ""))
                                 and not ANALYZER.analyze(m).newsworthy)
    assert changed == 1
    news = dict(store.connection.execute("SELECT id, newsworthy FROM reports").fetchall())
    assert news[ids["Prolific"]] == 0 and news[ids["Shooting"]] == 1


def test_possible_misses_say_when_older_ones_wait(main_window) -> None:
    from datetime import datetime, timedelta, timezone

    from s2_watch.analysis.base import AnalysisResult

    window = main_window()
    old = Message("JS8Call", "QST FLOODING ON THE SAN JUAN RIVER IN PAGOSA SPRINGS CO, ROADS CLOSED",
                  received_at=datetime.now(timezone.utc) - timedelta(days=9))
    window.store.save_missed_check("old", old, AnalysisResult(True, "Climate / Environment", "medium", "Flooding", 0.8),
                                   "new")
    window.misses_view.watch_hours = lambda: 48
    window.misses_view.refresh()
    assert window.misses_view.table.rowCount() == 0
    assert "1 older possible miss wait" in window.misses_view.older.text()
    window.misses_view.watch_hours = lambda: 24 * 14
    window.misses_view.refresh()
    assert window.misses_view.table.rowCount() == 1 and not window.misses_view.older.text()
