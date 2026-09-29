"""Daily copies of the S2 Watch database (settings, watch forms, reports, labels, history).

SQLite's online backup API copies a consistent snapshot while the app keeps running.
Copies go to a "backups" folder beside the database, one per day, newest kept.
"""

import sqlite3
import threading
from datetime import date
from pathlib import Path

KEEP_DAYS = 14
PREFIX = "s2_watch-"


def backup_folder(database_path: str | Path) -> Path:
    return Path(database_path).resolve().parent / "backups"


def backups(database_path: str | Path) -> list[Path]:
    """Existing backups, oldest first."""
    folder = backup_folder(database_path)
    return sorted(folder.glob(f"{PREFIX}????-??-??.db")) if folder.exists() else []


def back_up(database_path: str | Path, today: date | None = None, keep: int = KEEP_DAYS, force: bool = False) -> Path | None:
    """Copy the database to today's backup (once a day unless ``force``); prune old ones.

    Returns the backup written, or None when today's already exists.
    """
    today = today or date.today()
    folder = backup_folder(database_path)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{PREFIX}{today.isoformat()}.db"
    if target.exists() and not force:
        return None
    partial = target.with_suffix(".partial")
    source = sqlite3.connect(database_path, timeout=30)
    try:
        destination = sqlite3.connect(partial)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    partial.replace(target)
    for old in backups(database_path)[:-keep]:
        old.unlink(missing_ok=True)
    return target


def back_up_in_background(database_path: str | Path, on_done=None, force: bool = False) -> None:
    """Run ``back_up`` off the UI thread; ``on_done(path or None, error or None)``."""
    def work() -> None:
        try:
            result = back_up(database_path, force=force)
        except Exception as error:  # a full disk or a locked file must not stop the app
            if on_done:
                on_done(None, error)
            return
        if on_done:
            on_done(result, None)

    threading.Thread(target=work, daemon=True, name="s2-watch-backup").start()
