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
