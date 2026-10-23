import re

from s2_watch.ui.help_view import HELP_DIR, TOPICS, HelpDialog, load_topics, matching


def test_every_topic_loads_with_a_title_and_an_example() -> None:
    topics = load_topics()
    assert [topic for topic, _title, _html in topics] == list(TOPICS)
    for topic, title, html in topics:
        assert title and title != topic
        if topic != "shortcuts":
            assert 'class="example"' in html or "<table>" in html, topic
    assert sorted(path.stem for path in HELP_DIR.glob("*.html")) == sorted(TOPICS)   # no stray or missing files


def test_links_between_topics_lead_somewhere() -> None:
    for topic, _title, html in load_topics():
        for target in re.findall(r'href="help:([^"]+)"', html):
            assert target in TOPICS, f"{topic} links to help:{target}"


def test_search_finds_topics_with_every_word() -> None:
    topics = load_topics()
    assert "exercises" in matching(topics, "ENDEX")
    assert "reliability" in matching(topics, "track record")
    assert matching(topics, "flux capacitor") == []


def test_the_help_window_shows_topics_and_follows_links() -> None:
    dialog = HelpDialog()
    assert "Getting started" in dialog.page.toPlainText()
    dialog.show_topic("exercises")
    assert "EXERCISE EXERCISE EXERCISE" in dialog.page.toPlainText()
    dialog.search.setText("fix location")
    visible = [dialog.list.item(row).text() for row in range(dialog.list.count()) if not dialog.list.item(row).isHidden()]
    assert "Reliability tools" in visible and "Exercises" not in visible
    from PySide6.QtCore import QUrl

    dialog._link(QUrl("help:focus"))
    assert "Custom Area" in dialog.page.toPlainText()


def test_f1_opens_help(tmp_path) -> None:
    from s2_watch.ui.main_window import MainWindow

    window = MainWindow(str(tmp_path / "s2.db"))
    try:
        from PySide6.QtGui import QAction

        action = next(action for action in window.findChildren(QAction) if action.text() == "S2 Watch Help")
        assert action.shortcut().toString() == "F1"
        action.trigger()
        assert window._help_dialog is not None and window._help_dialog.isVisible()
    finally:
        window.close()
