import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from s2_watch.analysis.ollama import FallbackNewsAnalyzer, OllamaNewsAnalyzer
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.models import Message


class OllamaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        json.loads(self.rfile.read(length))
        body = json.dumps({"response": json.dumps({
            "newsworthy": True,
            "category": "Crime",
            "urgency": "high",
            "summary": "Fire reported",
            "confidence": 0.9,
            "matched_terms": ["fire"],
        })}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def test_ollama_analyzer_parses_response() -> None:
    server = HTTPServer(("127.0.0.1", 0), OllamaHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    result = OllamaNewsAnalyzer(endpoint=f"http://127.0.0.1:{server.server_port}").analyze(
        Message("JS8Call", "Fire report")
    )
    server.shutdown()
    thread.join()
    assert result.category == "Crime"
    assert result.confidence == 0.9


def test_ollama_falls_back_when_unavailable() -> None:
    analyzer = FallbackNewsAnalyzer(
        OllamaNewsAnalyzer(endpoint="http://127.0.0.1:1", timeout=0.01),
        RuleBasedNewsAnalyzer(),
    )
    result = analyzer.analyze(Message("JS8Call", "Flood evacuation"))
    assert result.newsworthy
    assert analyzer.last_error is not None


def test_hybrid_sends_only_flagged_messages_to_the_model() -> None:
    from s2_watch.analysis.base import AnalysisResult
    from s2_watch.analysis.ollama import HybridNewsAnalyzer, OllamaError

    class FakeModel:
        name = "ollama:test"

        def __init__(self, reply=None):
            self.seen, self.reply = [], reply

        def analyze(self, message, hint=None):
            self.seen.append((message.raw_content, hint.category if hint else None))
            if self.reply is None:
                raise OllamaError("not running")
            return self.reply

    routine = AnalysisResult(False, "routine", "low", "No change", 0.8)
    model = FakeModel(routine)
    hybrid = HybridNewsAnalyzer(model, RuleBasedNewsAnalyzer())
    assert hybrid.name == "ollama:test"
    assert not hybrid.analyze(Message("JS8Call", "W1AW: net check-in, 59 here")).newsworthy
    assert model.seen == []                                     # plain traffic: rules only
    # The rules see "terrorism"/"civil unrest"; the model reads the negation.
    assert hybrid.analyze(Message("FLMSG", "NO CHANGE: TERRORISM, CIVIL UNREST")) == routine
    assert len(model.seen) == 1 and model.seen[0][1] is not None   # given the rules' guess as a hint
    down = HybridNewsAnalyzer(FakeModel(None), RuleBasedNewsAnalyzer())
    assert down.analyze(Message("JS8Call", "Flood evacuation")).newsworthy   # rules' result stands
    assert down.last_error == "not running"


def test_model_results_for_plain_traffic_need_no_category() -> None:
    import pytest

    from s2_watch.analysis.ollama import OllamaError

    plain = OllamaNewsAnalyzer._validate_result(
        {"newsworthy": False, "category": "none", "urgency": "Low", "summary": "Check-in", "confidence": 0.9, "matched_terms": []})
    assert (plain.category, plain.urgency) == ("routine", "low")
    with pytest.raises(OllamaError):
        OllamaNewsAnalyzer._validate_result({"newsworthy": True, "category": "Weather", "urgency": "high",
                                             "summary": "x", "confidence": 0.5, "matched_terms": []})


def test_assessment_suggestion_replaces_subject_and_assessment() -> None:
    from s2_watch.ui.followup_dialog import replace_section

    report = "DTG: 1\nSUBJECT: Old\nLOCATION: Tulsa\n\nASSESSMENT:\n(add your assessment)"
    updated = replace_section(report, "Flooding closes roads in Tulsa", "River over its banks; NWS confirms.")
    assert "SUBJECT: Flooding closes roads in Tulsa" in updated
    assert updated.endswith("ASSESSMENT:\nRiver over its banks; NWS confirms.") and "LOCATION: Tulsa" in updated
