"""One Qt application for the whole test run.

Tests that build windows or dialogs need a QApplication; tests of background objects only
need Qt's core loop. Creating one QApplication up front serves both (a core-only
application created first would make later dialogs crash), headless.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])
