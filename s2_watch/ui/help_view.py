"""Help → S2 Watch Help (F1): the topics in s2_watch/data/help, searchable, with examples.

Each topic is one HTML file whose <h1> is its title. Links between topics are written
``help:<topic>``; web links open in the browser.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from s2_watch.ui.metrics_view import is_dark

HELP_DIR = Path(__file__).resolve().parent.parent / "data" / "help"
# Reading order of the topics.
TOPICS = ("getting-started", "sources", "watch-forms", "events", "confirmation", "labels", "briefings", "exercises",
          "reliability", "intelligence-entries", "focus", "map", "local-ai", "settings", "troubleshooting", "shortcuts")


def load_topics(directory: Path = HELP_DIR) -> list[tuple[str, str, str]]:
    """(topic id, title, html) in reading order."""
    topics = []
    for topic in TOPICS:
        path = directory / f"{topic}.html"
        if not path.is_file():
            continue
        html = path.read_text(encoding="utf-8")
        title = re.search(r"<h1>(.*?)</h1>", html, re.S)
        topics.append((topic, unescape(re.sub(r"<[^>]+>", "", title.group(1))) if title else topic, html))
    return topics


def plain_text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def matching(topics: list[tuple[str, str, str]], query: str) -> list[str]:
    """The topics that contain every word of the query (any order, capitals ignored)."""
    words = query.lower().split()
    return [topic for topic, title, html in topics
            if all(word in f"{title} {plain_text(html)}".lower() for word in words)]


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("S2 Watch Help")
        self.resize(1050, 760)
        self.topics = load_topics()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search help, e.g. exercise, track record, fix location")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.found = QLabel()
        self.found.setEnabled(False)
        self.list = QListWidget()
        for topic, title, _html in self.topics:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, topic)
            self.list.addItem(item)
        self.list.currentItemChanged.connect(lambda item, _previous: item and self.show_topic(item.data(Qt.ItemDataRole.UserRole)))
        self.page = QTextBrowser()
        self.page.setOpenLinks(False)
        self.page.anchorClicked.connect(self._link)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.found)
        left_layout.addWidget(self.list)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.page)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 790])
        layout = QHBoxLayout(self)
        layout.addWidget(splitter)
        self._current = ""
        if self.topics:
            self.list.setCurrentRow(0)
        else:
            self.page.setHtml("<p>The help files are missing from this installation.</p>")

    def _style(self) -> str:
        dark = is_dark(self)
        rule, link, code = ("#4a5560", "#8ab4f8", "#1e2124") if dark else ("#c5cdd6", "#1a5fb4", "#e4e7eb")
        return (f"h1 {{ font-size: 20pt; margin-bottom: 6px; }} h2 {{ font-size: 14pt; margin-top: 14px; }} "
                f"a {{ color: {link}; }} td, th {{ padding: 4px 6px; }} th {{ text-align: left; }} "
                f"table {{ border-color: {rule}; border-style: solid; }} "
                f"code, pre, kbd {{ background-color: {code}; }} "
                f"pre {{ margin: 4px; }}")

    def show_topic(self, topic: str) -> None:
        html = next((html for name, _title, html in self.topics if name == topic), None)
        if html is None:
            return
        self._current = topic
        # Rich text tables need their borders as attributes; examples get padding.
        html = html.replace("<table>", '<table border="1" cellspacing="0" cellpadding="4" width="100%">')
        # An example is one shaded box (rich text shades a one-cell table, not a whole div).
        shade = "#26323d" if is_dark(self) else "#eef4fb"
        html = html.replace('<div class="example">', f'<table width="100%" cellspacing="0" cellpadding="8" border="0" '
                                                      f'bgcolor="{shade}" style="margin: 8px 0;"><tr><td>')
        html = html.replace("</div>", "</td></tr></table>")
        self.page.document().setDefaultStyleSheet(self._style())
        self.page.setHtml(html)
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == topic and self.list.currentRow() != row:
                self.list.blockSignals(True)
                self.list.setCurrentRow(row)
                self.list.blockSignals(False)
        query = " ".join(self.search.text().split())
        if query:   # jump to the first place the search found: the phrase, else its first word
            if not self.page.find(query, QTextDocument.FindFlag(0)):
                self.page.find(query.split()[0], QTextDocument.FindFlag(0))

    def _filter(self, query: str) -> None:
        shown = set(matching(self.topics, query)) if query.strip() else {topic for topic, _t, _h in self.topics}
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(item.data(Qt.ItemDataRole.UserRole) not in shown)
        self.found.setText("" if not query.strip() else
                           f"{len(shown)} topic{'s' if len(shown) != 1 else ''} mention “{query.strip()}”" if shown
                           else "Nothing found; try fewer or other words.")
        first = next((self.list.item(row) for row in range(self.list.count()) if not self.list.item(row).isHidden()), None)
        if first is not None:
            topic = first.data(Qt.ItemDataRole.UserRole) if self._current not in shown else self._current
            self.show_topic(topic)

    def _link(self, url: QUrl) -> None:
        if url.scheme() == "help":
            self.show_topic(url.path() or url.toString().split(":", 1)[1])
        else:
            QDesktopServices.openUrl(url)
