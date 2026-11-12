from datetime import datetime, timezone

from PySide6.QtWidgets import QApplication, QMessageBox

from s2_watch.s2_report import briefing_report
from s2_watch.storage import SQLiteStore
from s2_watch.ui.drafts_dialog import DraftsDialog
from s2_watch.ui.followup_dialog import FollowUpDialog

NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)


def suggest(report, endpoint, model):   # stands in for Ollama
    return report.replace("(Analytical layer in measured language: assessed, likely, indicates, suggests. Overall "
                          "situation first.)", "Activity is assessed as steady.")


def test_a_briefing_is_saved_after_the_suggestion_and_reopens(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(tmp_path / "s2.db")
    store.set_setting("reports.folder", str(tmp_path / "reports"))
    text = briefing_report([], NOW, 24)
    dialog = FollowUpDialog(None, store, ollama=("http://x", "m"), text=text, suggest_fn=suggest, draft_kind="briefing",
                            export=True)
    assert dialog.unsaved() and dialog.save_button.isEnabled()
    dialog._suggested(suggest(text, "", ""), "")            # the suggestion arrives
    dialog.save_all()                                        # Save: kept in S2 Watch, every format written
    assert not dialog.unsaved() and dialog.save_button.text() == "Saved ✓"
    [saved] = store.list_drafts()
    [day] = (tmp_path / "reports").iterdir()                 # a folder for today (UTC)
    names = sorted(path.name for path in day.iterdir())
    stem = "MAGNET_S2_SITUATION_BRIEFING_UNITED_STATES_261011-1400Z"
    assert names == sorted(f"{stem}{end}" for end in (".txt", "_FLMSG.txt", "_JS8Call.txt", ".docx", ".html")), names
    assert sorted(saved["files"]) == sorted(str(day / name) for name in names)
    assert saved["kind"] == "briefing" and saved["subject"] == "Situation Briefing – the United States"
    assert saved["dtg"] == "261011-1400Z"
    assert "Activity is assessed as steady." in store.get_draft(saved["id"])["text"]

    # Reopened: nothing unsaved; an edit, then closing asks and saves to the same draft.
    reopened = FollowUpDialog(None, store, text=store.get_draft(saved["id"])["text"], draft_kind="briefing",
                              draft_id=saved["id"])
    assert not reopened.unsaved()
    reopened.full.setPlainText(reopened.full.toPlainText() + "\nTAGS: weather")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save))
    reopened.done(0)
    assert len(store.list_drafts()) == 1 and "TAGS: weather" in store.get_draft(saved["id"])["text"]

    listing = DraftsDialog(store, lambda value: str(value)[:16])
    assert listing.table.rowCount() == 1 and listing.table.item(0, 3).text() == saved["subject"]
    listing.table.selectRow(0)
    listing._delete()
    listing._delete()                                         # a second click confirms
    assert store.list_drafts() == []
    app.processEvents()


def test_the_precedence_is_chosen_in_the_draft_window_and_follows_the_text(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    store = SQLiteStore(tmp_path / "s2.db")
    from s2_watch.s2_report import js8_report

    dialog = FollowUpDialog(None, store, text=briefing_report([], NOW, 24), draft_kind="briefing", js8=js8_report)
    assert dialog.precedence.currentText() == "RR – ROUTINE"            # from the drafted report
    assert [dialog.precedence.itemText(i)[:2] for i in range(4)] == ["FL", "IM", "PR", "RR"]   # Flash: by hand only
    dialog.precedence.setCurrentIndex(dialog.precedence.findText("IM – IMMEDIATE"))
    dialog.precedence.activated.emit(dialog.precedence.currentIndex())  # what choosing it sends
    assert "PRECEDENCE: IM – IMMEDIATE" in dialog.full.toPlainText()
    assert dialog.js8call.toPlainText().startswith("MAGNET S2 261011-1400Z IM ") and dialog.unsaved()
    dialog.full.setPlainText(dialog.full.toPlainText().replace("PRECEDENCE: IM – IMMEDIATE", "PRECEDENCE: PR"))
    assert dialog.precedence.currentText() == "PR – PRIORITY"           # edited by hand: the picker follows
    plain = FollowUpDialog(None, store, text="Just some notes")
    assert not plain.top.isRowVisible(plain.precedence)                 # no PRECEDENCE line: no picker
    dialog._saved_text = dialog.full.toPlainText()
    dialog.deleteLater(); plain.deleteLater()
