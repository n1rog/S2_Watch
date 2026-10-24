"""One Qt application for the whole test run.

Tests that build windows or dialogs need a QApplication; tests of background objects only
need Qt's core loop. Creating one QApplication up front serves both (a core-only
application created first would make later dialogs crash), headless.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])


import pytest  # noqa: E402


@pytest.fixture
def main_window(tmp_path):
    """A full S2 Watch window on a fresh database, destroyed after the test.

    Closing alone leaves the window object alive with its timers and its map engine; a few
    of those left running across the run made later tests crash in Qt's event loop.
    """
    from PySide6.QtCore import QTimer

    from s2_watch.ui.main_window import MainWindow

    windows = []

    def open_window(database=None):
        window = MainWindow(str(database or tmp_path / "s2.db"))
        windows.append(window)
        return window

    yield open_window
    for window in windows:
        window.close()
        for timer in window.findChildren(QTimer):
            timer.stop()
        window.deleteLater()
    destroy_pending()


def destroy_pending() -> None:
    """Carry out deleteLater() now (processEvents alone doesn't): a web page still alive when
    the run ends crashes it."""
    from PySide6.QtCore import QCoreApplication, QEvent

    APP.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never go online: windows opened in tests would otherwise fetch the real feeds
    (slow, flaky, and their threads still mid-download when the run ends can crash it)."""
    import urllib.error
    import urllib.request

    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
