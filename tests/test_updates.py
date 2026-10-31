"""Finding a newer S2 Watch on GitHub and installing it from the update button."""

import io
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from s2_watch import __version__, updates


def wait_for(condition, seconds=10):
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.01)
    return condition()


def test_versions_compare_as_numbers() -> None:
    assert updates.parse_version("v0.41.0") == (0, 41, 0) == updates.parse_version("0.41.0")
    assert updates.parse_version("v1.2") is None and updates.parse_version("latest") is None
    assert updates.newer("0.100.0", "0.41.0") and not updates.newer("0.9.0", "0.41.0")
    assert not updates.newer(__version__)


def test_the_latest_version_is_the_highest_version_tag(monkeypatch) -> None:
    tags = [{"name": "v0.9.0"}, {"name": "v0.41.0"}, {"name": "v0.100.1"}, {"name": "test-build"}]
    monkeypatch.setattr(updates, "_get_json", lambda url, timeout: tags)
    assert updates.latest_version() == "0.100.1"


def test_whats_new_lists_each_versions_headline_newest_first(monkeypatch) -> None:
    commits = [{"commit": {"message": m}} for m in
               ("Version 0.42.0: radar\n\nDetails", "Fix a typo", "Version 0.43.0: buttons")]
    monkeypatch.setattr(updates, "_get_json", lambda url, timeout: {"commits": commits})
    assert updates.changes("0.43.0", "0.41.0") == ["Version 0.43.0: buttons", "Version 0.42.0: radar"]

    def offline(url, timeout):
        raise OSError("offline")

    monkeypatch.setattr(updates, "_get_json", offline)
    assert updates.changes("0.43.0", "0.41.0") == []


def release(tmp_path: Path, version: str, installer: str = "echo installed", extra=()) -> Path:
    """A tar.gz shaped like GitHub's archive of a version tag."""
    archive = tmp_path / "release.tar.gz"
    files = {"S2_Watch-x/installer/install.sh": installer,
             "S2_Watch-x/s2_watch/__init__.py": f'"""S2 Watch."""\n__version__ = "{version}"\n', **dict(extra)}
    with tarfile.open(archive, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive


def test_a_download_is_unpacked_only_if_it_is_the_version_asked_for(tmp_path: Path) -> None:
    folder = updates.unpack(release(tmp_path, "0.99.0"), "0.99.0", tmp_path / "a")
    assert (folder / "installer/install.sh").is_file()
    with pytest.raises(ValueError, match="version 0.99.0, not 0.98.0"):
        updates.unpack(release(tmp_path, "0.99.0"), "0.98.0", tmp_path / "b")
    with pytest.raises(tarfile.TarError):
        updates.unpack(release(tmp_path, "0.99.0", extra={"../escape.txt": "x"}), "0.99.0", tmp_path / "c")
    assert not (tmp_path / "escape.txt").exists()


def test_the_updater_waits_for_s2_watch_to_close_then_backs_up_installs_and_reopens(tmp_path: Path) -> None:
    installer = 'echo "$@" > "$(dirname "$0")/../../../installed.txt"'
    source = updates.unpack(release(tmp_path, "0.99.0", installer), "0.99.0", tmp_path / "update")
    database = tmp_path / "data/s2_watch.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        connection.execute("INSERT INTO settings VALUES ('kept', 'yes')")
    connection.close()
    app = subprocess.Popen(["sleep", "1.5"])   # stands in for S2 Watch
    log = tmp_path / "data/update.log"
    updater = updates.start_updater(source, database, "0.99.0", app.pid, log,
                                    relaunch=["touch", str(tmp_path / "reopened")])
    time.sleep(0.5)
    assert not (tmp_path / "update/installed.txt").exists()   # S2 Watch still open
    app.wait()
    updater.wait(30)
    assert (tmp_path / "update/installed.txt").read_text(encoding="utf-8").split() == ["--yes", "--no-wizard"]
    backup = tmp_path / "data/backups/s2_watch-before-0.99.0.db"
    assert sqlite3.connect(backup).execute("SELECT value FROM settings").fetchone() == ("yes",)
    assert (tmp_path / "reopened").exists()
    text = log.read_text(encoding="utf-8")
    assert "updating to 0.99.0" in text and "installer finished with status 0" in text


def test_reopening_after_an_update_says_whether_it_worked() -> None:
    assert updates.update_outcome("", "0.41.0") is None
    assert updates.update_outcome("0.42.0", "0.42.0") == "updated"
    assert updates.update_outcome("0.42.0", "0.41.0") == "failed"


def test_the_button_turns_orange_when_an_update_is_out(tmp_path, main_window, monkeypatch) -> None:
    monkeypatch.setattr(updates, "latest_version", lambda timeout=15: "99.0.0")
    monkeypatch.setattr(updates, "changes", lambda new, old=__version__, timeout=15: ["Version 99.0.0: everything"])
    window = main_window(tmp_path / "update.db")
    button = window.update_button
    assert button.text() == "Up to date" and not button.styleSheet()
    button.check()
    assert wait_for(lambda: button.available == "99.0.0")
    assert button.text() == "Update to 99.0.0" and "#ef6c00" in button.styleSheet()

    monkeypatch.setattr(updates, "latest_version", lambda timeout=15: __version__)
    button.available = None
    button.check()
    assert wait_for(lambda: button.text() == "Up to date")
    assert not button.styleSheet()

    def offline(timeout=15):
        raise OSError("no route to host")

    monkeypatch.setattr(updates, "latest_version", offline)
    button.check()
    assert wait_for(lambda: button.text() == "Updates?")
    assert "no route to host" in button.toolTip()


def test_clicking_the_button_downloads_installs_and_closes(tmp_path, main_window, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from s2_watch.ui.update_button import UpdateButton

    window = main_window(tmp_path / "click.db")
    button = window.update_button
    button.available = "99.0.0"
    started, closed = [], []
    monkeypatch.setattr(updates, "install_kind", lambda: "installer")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(updates, "download", lambda version: tmp_path / "source")
    monkeypatch.setattr(updates, "start_updater", lambda *args: started.append(args))
    window.close = lambda: closed.append(True)   # the fixture closes it for real afterwards
    button.click()
    assert wait_for(lambda: closed)
    del window.close
    source, database, version, pid, log = started[0]
    assert (source, version, database.name, log.name) == (tmp_path / "source", "99.0.0", "click.db", "update.log")
    assert window.store.get_setting("updates.pending", "") == "99.0.0"

    # S2 Watch reopens: the version it went to install isn't the one running, so it says so.
    UpdateButton(window, first_check_ms=-1)
    assert "didn't install" in window.statusBar().currentMessage()
    assert window.store.get_setting("updates.pending", "") == ""
    window.store.set_setting("updates.pending", __version__)
    UpdateButton(window, first_check_ms=-1)
    assert window.statusBar().currentMessage() == f"Updated to S2 Watch {__version__}."
