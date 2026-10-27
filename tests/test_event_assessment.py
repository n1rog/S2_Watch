"""Ollama's assessment of a whole event: the prompt, checking the reply, the panel and the draft."""

import json
from datetime import timedelta
from pathlib import Path

from PySide6.QtWidgets import QApplication

from s2_watch import event_assessment
from s2_watch.event_assessment import EventAssessment, event_brief, fill_report, parse_assessment
from s2_watch.s2_report import parse_report
from s2_watch.storage import SQLiteStore

from tests.test_events import T0, TULSA, add

REPLY = """**BLUF:** Flooding on the river in Tulsa is assessed as continuing [1, 2]; NWS confirms it [3].
TREND: Escalating - road closures followed the first report [1], [2].
AGREEMENT: All three reports agree on the river flooding [1-3]; none contradict.
ASSESSMENT:
Radio reported the flooding before the official warning [1, 3], which indicates early local awareness [9].
Road closures suggest worsening access [2].
PROBABILITY:
Flooding spreads to adjacent roads | moderate
HIGH: Riverside Dr stays closed [2]
what may happen next | maybe
GAPS:
- Are shelters open?
- Are shelters open?
MITIGATION:
- Avoid Riverside Dr [2].
GUIDANCE:
1. Monitor the county net for road reports.
=== END ==="""


def event_with_three_reports(tmp_path: Path):
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = add(store, "MagCommstat", "TULSA OK - RIVER FLOODING", T0, TULSA, fields={"sender": "K1ABC"})
    add(store, "MagCommstat", "TULSA OK - RIVERSIDE DR CLOSED, FLOODING", T0 + timedelta(hours=1), TULSA,
        fields={"sender": "W2XYZ"})
    add(store, "OSINT", "NWS: Flood Warning for Tulsa County, OK", T0 + timedelta(hours=2), TULSA,
        fields={"sender": "NWS"})
    [event] = [e for e in store.events_since(T0 - timedelta(days=1)) if e.event_id == event_id]
    return store, event, store.event_reports(event_id)


def test_the_prompt_numbers_the_reports_and_keeps_the_rules(tmp_path) -> None:
    _store, event, reports = event_with_three_reports(tmp_path)
    brief = event_brief(event, reports, ["Earlier related activity: none.", "(Add what is normal.)"])
    assert "[1] 011200Z Oct — MagCommstat (K1ABC)" in brief and "[3] " in brief and "[4] " not in brief
    assert "The current situation is report [3]" in brief and "REPORTS, oldest to newest" in brief
    assert "- Earlier related activity: none." in brief and "Add what is normal" not in brief
    assert "using ONLY the numbered reports" in brief and "assessed, likely" in brief and "PACE" in brief


def test_the_reply_is_checked_citations_kept_only_for_real_reports() -> None:
    assessment = parse_assessment(REPLY, 3, "llama3.2")
    assert assessment.trend == "escalating" and assessment.trend_reason == "road closures followed the first report [1, 2]."
    assert assessment.assessment[0].endswith("which indicates early local awareness.")    # [9] doesn't exist
    assert assessment.agreement.startswith("All three reports agree on the river flooding [1, 2, 3]")
    assert assessment.bluf.startswith("Flooding on the river")                             # markdown stripped
    assert assessment.probabilities == [("Flooding spreads to adjacent roads", "MODERATE"),
                                        ("Riverside Dr stays closed [2]", "HIGH")]          # "maybe" dropped
    assert assessment.gaps == ["Are shelters open?"]                                       # repeats dropped
    assert assessment.guidance == ["Monitor the county net for road reports."]
    assert EventAssessment.from_json(assessment.to_json()) == assessment


def test_a_draft_gets_the_analytical_sections_citing_its_source_list(tmp_path) -> None:
    from s2_watch.followup import FollowUpFacts
    from s2_watch.analysis.base import AnalysisResult
    from s2_watch.s2_report import followup_report

    store, event, reports = event_with_three_reports(tmp_path)
    found = store.report_first_message(reports[0]["report_id"])
    facts = FollowUpFacts(found[0], AnalysisResult(True, "Climate / Environment", "high", "Flooding", 0.9), [])
    text = fill_report(followup_report(facts, reports), parse_assessment(REPLY, 3, "m"), reports)
    sections = dict(parse_report(text).sections)
    probability = [b for b in sections["COMMENTS / ASSESSMENT"] if b.kind == "probability"][0]
    assert probability.rows[0] == ("Flooding spreads to adjacent roads", "MODERATE")
    assert "Road closures suggest worsening access [2]." in text            # report 2 is source line 2
    assert "Trend: escalating." in text and "- Avoid Riverside Dr [2]." in text
    assert "- Monitor the county net for road reports." in text and "(Operator-specific actions" not in text


def test_the_event_panel_assesses_in_the_background_and_says_when_it_is_out_of_date(tmp_path, monkeypatch) -> None:
    from s2_watch.ui.event_detail import EventDetail

    app = QApplication.instance() or QApplication([])
    store, event, reports = event_with_three_reports(tmp_path)
    monkeypatch.setattr(event_assessment, "ollama_generate", lambda *args, **kwargs: REPLY)
    monkeypatch.setattr(event_assessment, "make_room", lambda endpoint, model: None)
    detail = EventDetail(store, lambda value: str(value)[:16])
    detail.assessment_brief = lambda shown: (event_brief(shown, reports), len(reports))
    detail.ollama_settings = lambda: ("http://127.0.0.1:11434", "llama3.2")
    detail.show_event(event)
    assert detail.assess_button.isEnabled() and "[3] " in detail.browser.toPlainText()   # reports numbered
    detail.assess()
    for _ in range(200):
        app.processEvents()
        if store.event_assessment(event.event_id) is not None and not detail._assessing:
            break
    text = detail.browser.toPlainText()
    assert "Assessment — Ollama suggestion (llama3.2)" in text and "Trend: escalating" in text
    assert "Flooding spreads to adjacent roads" in text and "Out of date" not in text
    add(store, "MagCommstat", "TULSA OK - FLOODING WORSE", T0 + timedelta(hours=3), TULSA, fields={"sender": "K1ABC"})
    [newer] = [e for e in store.events_since(T0 - timedelta(days=1)) if e.event_id == event.event_id]
    detail.show_event(newer)
    assert "Out of date: 1 new report since" in detail.browser.toPlainText()
    detail.deleteLater()


def test_a_long_event_shows_the_model_its_first_and_latest_reports(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s2.db")
    event_id = add(store, "OSINT", "NHC: Hurricane Vance 95 kt", T0, TULSA, fields={"sender": "NHC"})
    for hour in range(1, 12):
        add(store, "OSINT", f"NHC: Hurricane Vance update {hour}", T0 + timedelta(hours=hour), TULSA,
            fields={"sender": "NHC"})
    reports = store.event_reports(event_id)
    [event] = [e for e in store.events_since(T0 - timedelta(days=1)) if e.event_id == event_id]
    brief = event_brief(event, reports)
    assert "FIRST REPORT:\n[1] " in brief and "(5 reports in between not shown)" in brief
    assert "[7] " in brief and "[12] " in brief and "[6] " not in brief
    assert "The current situation is report [12]" in brief


def test_an_assessment_unloads_other_models_and_refuses_when_memory_is_short(monkeypatch) -> None:
    import pytest

    from s2_watch.analysis.ollama import OllamaError

    calls = []
    GB = 1024 ** 3

    def fake(endpoint, path, payload=None, timeout=30.0):
        calls.append((path, payload))
        if path == "/api/ps":
            return {"models": [{"name": "llama3.2:latest", "model": "llama3.2:latest"}]}
        if path == "/api/tags":
            return {"models": [{"name": "qwen2.5:7b", "model": "qwen2.5:7b", "size": int(4.7 * GB)}]}
        return {}
    monkeypatch.setattr(event_assessment, "_ollama_json", fake)
    monkeypatch.setattr(event_assessment, "available_memory", lambda: 20 * GB)
    event_assessment.make_room("http://127.0.0.1:11434", "qwen2.5:7b")
    assert ("/api/generate", {"model": "llama3.2:latest", "keep_alive": 0}) in calls     # the other model unloaded
    monkeypatch.setattr(event_assessment, "available_memory", lambda: 5 * GB)
    with pytest.raises(OllamaError, match="not enough free memory for qwen2.5:7b"):
        event_assessment.make_room("http://127.0.0.1:11434", "qwen2.5:7b")
    with pytest.raises(OllamaError, match="ollama pull mistral"):
        event_assessment.make_room("http://127.0.0.1:11434", "mistral")
