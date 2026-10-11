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
    text = briefing_report([], NOW, 24)
    dialog = FollowUpDialog(None, store, ollama=("http://x", "m"), text=text, suggest_fn=suggest, draft_kind="briefing")
    assert dialog.unsaved() and dialog.save_button.isEnabled()
    dialog._suggested(suggest(text, "", ""), "")            # the suggestion arrives
    dialog.save_draft()
    assert not dialog.unsaved() and dialog.save_button.text() == "Saved in S2 Watch ✓"
    [saved] = store.list_drafts()
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
