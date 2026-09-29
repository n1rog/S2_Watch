import sqlite3
from datetime import date, timedelta

from s2_watch.backups import back_up, backup_folder, backups
from s2_watch.storage import SQLiteStore


def test_daily_backup_is_a_readable_copy_and_old_ones_are_pruned(tmp_path) -> None:
    database = tmp_path / "s2_watch.db"
    store = SQLiteStore(database)
    store.set_setting("focus.regions", "6")   # the app keeps its connection open meanwhile
    day = date(2026, 9, 1)
    first = back_up(database, today=day)
    assert first == backup_folder(database) / "s2_watch-2026-09-01.db"
    copy = sqlite3.connect(first)
    assert copy.execute("SELECT value FROM settings WHERE key = 'focus.regions'").fetchone() == ("6",)
    copy.close()
    assert back_up(database, today=day) is None           # once a day
    assert back_up(database, today=day, force=True) == first
    for offset in range(1, 20):
        back_up(database, today=day + timedelta(days=offset), keep=14)
    kept = backups(database)
    assert len(kept) == 14 and kept[-1].name == "s2_watch-2026-09-20.db"
    store.close()
