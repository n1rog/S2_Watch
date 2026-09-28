from pathlib import Path

from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.digest import digest_label, split_digest
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.monitoring import Monitor
from s2_watch.storage import SQLiteStore

BRIEFING = (
    "CUSTOM_FORM,magnet_general_V1.1.1.html\nL01,260921-100z\nL02,MR05 MR07\nL03,W1KEB\nL06,WEEKLY BRIEFING\n"
    "L07,MAGNET WEEKLY BRIEFING BY W1KEB  9/14–9/20/26\\nMN — New Richland emergency: ~9\\\" rain, 36 evacuated (KSTP)"
    "\\nWI — Baier, 83, arrested in 1995 Ruland homicide (WSAW)\\nIA — Vance rallies in Council Bluffs (IPR)"
    "\\nOH — Data center measures on 18 ballots (OCJ)\nL04,Routine\nL05,MR05"
)


def test_flmsg_briefing_splits_into_items() -> None:
    items = split_digest(Message("FLMSG", BRIEFING, fields={"filename": "a.k2s"}))
    assert [item.raw_content for item in items] == [
        'MN — New Richland emergency: ~9" rain, 36 evacuated (KSTP)',
        "WI — Baier, 83, arrested in 1995 Ruland homicide (WSAW)",
        "IA — Vance rallies in Council Bluffs (IPR)",
        "OH — Data center measures on 18 ballots (OCJ)",
    ]
    assert items[2].fields["message_id"] == "a.k2s#3"
    assert digest_label(items[2]) == "from “MAGNET WEEKLY BRIEFING BY W1KEB  9/14–9/20/26” (item 3 of 4)"


def test_section_headings_are_kept_apart_from_item_text() -> None:
    text = ("U.S. EmComm DocDrop News — Sep 20-26, 2026\n\nCYBER: Stevens Point, WI — unauthorized network access 9/23.\n\n"
            "DOMESTIC TERRORISM: No verified plot/attack in region this period per reviewed sources.\n\n"
            "CIVIL UNREST: Broadview, IL ICE facility protest 9/20 — 2 hospitalized.\n\nEND REPORT")
    items = split_digest(Message("FLMSG", text))
    assert [item.fields["section"] for item in items] == ["Cyber", "Domestic Terrorism", "Civil Unrest"]
    assert items[1].raw_content.startswith("No verified plot")
    # Without its heading, the "no plot" item is not taken for terrorism.
    assert not RuleBasedNewsAnalyzer().analyze(items[1]).newsworthy


def test_single_reports_are_not_split() -> None:
    assert split_digest(Message("JS8Call", "MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL")) is None
    assert split_digest(Message("JS8Call", "KI6QDB: long single report about a protest downtown. " * 8)) is None


def test_monitor_reports_each_item_and_credits_the_matching_category_form(tmp_path: Path) -> None:
    class Source:
        application_name = "FLMSG"

        def __init__(self):
            self.sent = False

        def read_messages(self):
            if self.sent:
                return []
            self.sent = True
            return [Message("FLMSG", BRIEFING, fields={"filename": "a.k2s"})]

    forms = [WatchForm(ANY_APPLICATION, category, pattern, use_regex=True) for category, pattern in CATEGORY_PATTERNS.items()]
    store = SQLiteStore(tmp_path / "s2.db")
    shown = []
    Monitor([Source()], forms, store, shown.append, RuleBasedNewsAnalyzer()).poll()
    by_text = {message.raw_content[:12]: message.form_name for message in shown}
    assert by_text["MN — New Ric"] == "Climate / Environment"   # evacuated
    assert by_text["WI — Baier, "] == "Crime"                   # arrested, homicide
    assert by_text["IA — Vance r"] == "Civil Unrest"            # rallies
    assert shown[-1].form_name == "Digest"                      # the whole digest, kept for History
    history = [row[2] for row in store.search_history()]
    assert "Digest" in history and history.count("Digest") == 1


def test_digest_items_are_placed_by_their_state_prefix() -> None:
    from s2_watch.locating import LocationResolver

    items = split_digest(Message("FLMSG", BRIEFING, fields={"filename": "a.k2s"}))
    labels = [[location.label for location in LocationResolver().resolve(item)] for item in items]
    assert labels[0] == ["Minnesota"] and labels[1] == ["Wisconsin"] and labels[3] == ["Ohio"]
