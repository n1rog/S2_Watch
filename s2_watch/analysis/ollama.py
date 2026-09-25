import json
from http.client import HTTPConnection, HTTPSConnection, RemoteDisconnected
from typing import Any
from urllib.parse import urlparse

from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.models import Message


class OllamaError(RuntimeError):
    """Raised when Ollama cannot provide a valid analysis."""


class OllamaNewsAnalyzer(NewsAnalyzer):
    def __init__(
        self,
        model: str = "llama3.2",
        endpoint: str = "http://127.0.0.1:11434",
        timeout: float = 10.0,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def analyze(self, message: Message) -> AnalysisResult:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise OllamaError(f"Invalid Ollama endpoint: {self.endpoint}")
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "prompt": (
                "Classify this radio report as JSON only. Use exactly these fields: "
                f"category must be one of: {', '.join(NEWS_CATEGORIES)}; "
                "newsworthy (boolean), urgency (low|medium|high), "
                "summary (string), confidence (number from 0 to 1), "
                "matched_terms (array of strings). Do not invent facts.\n\n"
                f"Report: {message.raw_content}"
            ),
        }
        try:
            connection_type = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
            connection = connection_type(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                timeout=self.timeout,
            )
            connection.request(
                "POST",
                f"{parsed.path or ''}/api/generate",
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read()
        except (OSError, RemoteDisconnected) as error:
            raise OllamaError(f"Unable to contact Ollama: {error}") from error
        finally:
            if "connection" in locals():
                connection.close()
        if response.status != 200:
            raise OllamaError(f"Ollama returned HTTP {response.status}")
        try:
            outer = json.loads(body)
            result = json.loads(outer["response"]) if isinstance(outer.get("response"), str) else outer["response"]
            return self._validate_result(result)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OllamaError("Ollama returned invalid analysis JSON") from error

    @staticmethod
    def _validate_result(result: Any) -> AnalysisResult:
        if not isinstance(result, dict):
            raise OllamaError("Ollama analysis is not an object")
        newsworthy = result.get("newsworthy")
        category = result.get("category")
        urgency = result.get("urgency")
        summary = result.get("summary")
        confidence = result.get("confidence")
        terms = result.get("matched_terms", [])
        if (
            not isinstance(newsworthy, bool)
            or category not in NEWS_CATEGORIES
            or urgency not in {"low", "medium", "high"}
            or not isinstance(summary, str)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
            or not isinstance(terms, list)
            or not all(isinstance(term, str) for term in terms)
        ):
            raise OllamaError("Ollama analysis fields failed validation")
        return AnalysisResult(
            newsworthy=newsworthy,
            category=category,
            urgency=urgency,
            summary=summary,
            confidence=float(confidence),
            matched_terms=tuple(terms),
        )


class FallbackNewsAnalyzer(NewsAnalyzer):
    """Uses a preferred analyzer and falls back when it reports an integration error."""

    def __init__(self, preferred: NewsAnalyzer, fallback: NewsAnalyzer) -> None:
        self.preferred = preferred
        self.fallback = fallback
        self.last_error: str | None = None

    def analyze(self, message: Message) -> AnalysisResult:
        try:
            self.last_error = None
            return self.preferred.analyze(message)
        except OllamaError as error:
            self.last_error = str(error)
            return self.fallback.analyze(message)
