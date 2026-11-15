"""Tools → Fetch Missed News: GDELT's news for a stretch S2 Watch didn't save (no network here)."""

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

from s2_watch.adapters import osint
from s2_watch.models import Message

START = datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)


def fake_gdelt(monkeypatch, refuse_after=None):
    asked = []

    def get(url, timeout=25):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        asked.append(query)
        if refuse_after is not None and len(asked) > refuse_after:
            raise OSError("HTTP Error 429: Too Many Requests")
        title = ("Airlines work through cancellations after nationwide ground stop" if "ground stop" in query["query"]
                 else f"Story {len(asked)}")
        return json.dumps({"articles": [{"title": title, "url": f"https://news.example/{len(asked)}",
                                         "seendate": query["startdatetime"][:8] + "T120000Z",
                                         "domain": "news.example"}]}).encode()

    monkeypatch.setattr(osint, "_get", get)
    monkeypatch.setattr(osint.time, "sleep", lambda _seconds: None)
    return asked


def test_a_missed_stretch_is_searched_window_by_window(monkeypatch) -> None:
    asked = fake_gdelt(monkeypatch)
    done = []
    messages = osint.fetch_gdelt_window(START, END, progress=lambda d, t: done.append((d, t)))
    assert len(asked) == osint.backfill_requests(START, END) == 21     # 7 windows of 6 hours, 3 searches each
    assert asked[0]["startdatetime"] == "20261113050000" and asked[0]["enddatetime"] == "20261113110000"
    assert "timespan" not in asked[0] and done[-1] == (21, 21)
    assert any("ground stop" in message.raw_content for message in messages)
    assert {message.fields["source"] for message in messages} == {"GDELT news (fetched later)"}


def test_gdelt_refusing_ends_the_fetch_politely(monkeypatch) -> None:
    asked = fake_gdelt(monkeypatch, refuse_after=4)
    messages = osint.fetch_gdelt_window(START, END)
    assert len(asked) == 5 and len(messages) == 4       # stopped at the first refusal, kept what came back


def test_the_gap_is_found_from_the_news_saved(tmp_path) -> None:
    from s2_watch.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "s2.db")
    now = datetime(2026, 11, 15, 4, tzinfo=timezone.utc)
    for hour in range(7 * 24):
        when = now - timedelta(hours=7 * 24 - hour)
        quiet = START <= when < END
        for index in range(2 if quiet else 30):
            store.save_message(Message("OSINT", f"s{hour}-{index}", received_at=when + timedelta(minutes=index)),
                               f"{hour}-{index}")
    assert store.news_gap(now) == (START, END)


def test_fetched_stories_reach_the_osint_source_once(monkeypatch, main_window) -> None:
    from s2_watch.ui.backfill_dialog import BackfillDialog

    window = main_window()
    adapter = osint.OsintAdapter(timedelta(hours=48), {})
    stories = [Message("OSINT", "Airlines work through cancellations after nationwide ground stop",
                       received_at=START + timedelta(hours=10),
                       fields={"type": "NEWS", "message_id": "https://news.example/1", "source": "GDELT news (fetched later)"})]
    dialog = BackfillDialog((START, END), adapter.inject, window, fetch=lambda s, e, progress, stopped: stories,
                            now=END + timedelta(hours=8))
    assert "21 requests" in dialog.estimate.text()
    dialog.start_fetch()
    dialog._thread.join(5)
    dialog._poll()
    assert "1 new" in dialog.result.text()
    assert [message.raw_content for message in adapter.read_messages()] == [stories[0].raw_content]
    assert adapter.inject(stories) == 0                  # already delivered
