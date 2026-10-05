"""Analysis with a local Ollama model, used together with the keyword rules.

The rules run first and decide quickly which messages are plain traffic; the model reads
only the messages the rules flag, where understanding matters (negations such as "NO
CHANGE: TERRORISM", paraphrases, ham shorthand). If Ollama is slow, stopped or returns
something unusable, the rules' result stands. Nothing leaves the computer.
"""

import json
from http.client import HTTPConnection, HTTPSConnection, RemoteDisconnected
from typing import Any
from urllib.parse import urlparse

from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.models import Message

# Keep the model in memory between messages (loading it takes many seconds on a CPU).
KEEP_ALIVE = "30m"

PROMPT = """You classify amateur-radio emergency-net traffic for a news watch officer in the United States.

About the traffic:
- Messages come from JS8Call, Commstat/MagCommstat StatReps, FLMSG forms and net briefings.
- Callsigns (e.g. N1ROG), "@MAGNET"/"@MR06"-style group addresses, grid squares (EM29RL), "DE", "*DE*", "MSG", "{{&%}}" and similar are routing, not content.
- "MR01".."MR10" are MARS regions (FEMA regions 1-10).
- Briefings often list items that did NOT change. A topic named after "NO CHANGE", "no reports of", "none", "negative" or "all quiet" is NOT news.
- Routine traffic (net check-ins, signal reports, "ALL GREEN", tests, greetings) is not newsworthy.
- Chat and questions about past events or personal experience ("have you ever...", "how often do you...") are not news.
- For a briefing with several topics, judge only the items that report a change; categorise by those items, not by topics listed as unchanged.

Decide whether the message reports a real, current event the officer should know about.
category: the best fit from: {categories}.
urgency: high ONLY for an imminent threat to life or a major infrastructure failure happening now; medium = significant developing event; low = minor, distant, contained or informational.
summary: one plain headline, at most 15 words, with the place if known. Do not add facts that are not in the message.
matched_terms: the few words that show the event.
confidence: 0 to 1.
{hint}
Answer with JSON only: {{"newsworthy": true|false, "category": "...", "urgency": "low|medium|high", "summary": "...", "confidence": 0.0, "matched_terms": ["..."]}}

Message: {message}"""


class OllamaError(RuntimeError):
    """Raised when Ollama cannot provide a valid analysis."""


def ollama_generate(endpoint: str, model: str, prompt: str, timeout: float, json_format: bool = False) -> str:
    """One completion from Ollama's /api/generate; raises OllamaError when it can't."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OllamaError(f"Invalid Ollama endpoint: {endpoint}")
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "keep_alive": KEEP_ALIVE,
                               "options": {"temperature": 0}}
    if json_format:
        payload["format"] = "json"
    connection = None
    try:
        connection_type = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                     timeout=timeout)
        connection.request("POST", f"{parsed.path.rstrip('/')}/api/generate", body=json.dumps(payload),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
    except (OSError, RemoteDisconnected) as error:
        raise OllamaError(f"Unable to contact Ollama: {error}") from error
    finally:
        if connection is not None:
            connection.close()
    if response.status != 200:
        raise OllamaError(f"Ollama returned HTTP {response.status}")
    try:
        text = json.loads(body)["response"]
    except (KeyError, TypeError, ValueError) as error:
        raise OllamaError("Ollama returned an unexpected reply") from error
    if not isinstance(text, str):
        raise OllamaError("Ollama returned an unexpected reply")
    return text


class OllamaNewsAnalyzer(NewsAnalyzer):
    def __init__(
        self,
        model: str = "llama3.2",
        endpoint: str = "http://127.0.0.1:11434",
        timeout: float = 45.0,   # the first message also loads the model
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def analyze(self, message: Message, hint: AnalysisResult | None = None) -> AnalysisResult:
        suggestion = ""
        if hint is not None and hint.newsworthy:
            suggestion = (f'A keyword filter matched {", ".join(hint.matched_terms[:6]) or "some words"} and suggested '
                          f'"{hint.category}"; keywords can be wrong (negations, unrelated uses), so decide yourself.')
        prompt = PROMPT.format(categories=", ".join(NEWS_CATEGORIES), hint=suggestion, message=message.raw_content)
        text = ollama_generate(self.endpoint, self.model, prompt, self.timeout, json_format=True)
        try:
            return self._validate_result(json.loads(text))
        except (TypeError, ValueError) as error:
            raise OllamaError("Ollama returned invalid analysis JSON") from error

    @staticmethod
    def _validate_result(result: Any) -> AnalysisResult:
        if not isinstance(result, dict):
            raise OllamaError("Ollama analysis is not an object")
        newsworthy = result.get("newsworthy")
        category = result.get("category")
        urgency = str(result.get("urgency", "")).lower()
        summary = result.get("summary")
        confidence = result.get("confidence")
        terms = result.get("matched_terms", [])
        if newsworthy is False and category not in NEWS_CATEGORIES:
            category = "routine"   # plain traffic needs no category
        if (
            not isinstance(newsworthy, bool)
            or (category not in NEWS_CATEGORIES and category != "routine")
            or urgency not in {"low", "medium", "high"}
            or not isinstance(summary, str)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
            or not isinstance(terms, list)
            or not all(isinstance(term, str) for term in terms)
        ):
            raise OllamaError("Ollama analysis fields failed validation")
        if not newsworthy:
            category, urgency = "routine", "low"
        return AnalysisResult(
            newsworthy=newsworthy,
            category=category,
            urgency=urgency,
            summary=summary.strip(),
            confidence=float(confidence),
            matched_terms=tuple(terms),
        )


class HybridNewsAnalyzer(NewsAnalyzer):
    """Rules first; the model reads only messages the rules flag (newsworthy or any match).

    Plain traffic, most of what the feeders carry, never waits for the model, which keeps
    S2 Watch responsive on a computer without a GPU the model can use.
    """

    def __init__(self, model: OllamaNewsAnalyzer, rules: NewsAnalyzer) -> None:
        self.model = model
        self.rules = rules
        self.last_error: str | None = None

    @property
    def name(self) -> str:
        return self.model.name

    def analyze(self, message: Message) -> AnalysisResult:
        first = self.rules.analyze(message)
        if not first.newsworthy and not first.matched_terms:
            return first
        # Official feeds (OSINT) are structured and arrive in bursts (20+ wildfires in one
        # update); at several seconds a message on a CPU they would hold up radio traffic,
        # and the model adds little to them. The model reads the radio messages.
        if message.source_application == "OSINT":
            return first
        try:
            result = self.model.analyze(message, hint=first)
            self.last_error = None
            return result
        except OllamaError as error:
            self.last_error = str(error)
            return first


class FallbackNewsAnalyzer(NewsAnalyzer):
    """Uses a preferred analyzer and falls back when it reports an integration error."""

    def __init__(self, preferred: NewsAnalyzer, fallback: NewsAnalyzer) -> None:
        self.preferred = preferred
        self.fallback = fallback
        self.last_error: str | None = None

    @property
    def name(self) -> str:
        return getattr(self.preferred, "name", "fallback")

    def analyze(self, message: Message) -> AnalysisResult:
        try:
            self.last_error = None
            return self.preferred.analyze(message)
        except OllamaError as error:
            self.last_error = str(error)
            return self.fallback.analyze(message)


ASSESSMENT_PROMPT = """You help an amateur-radio emergency-net operator write a follow-up report.
Below is their draft. Write two things, using only facts in the draft:
SUBJECT: one clear headline (at most 12 words, with the place).
ASSESSMENT: two to four short sentences: what happened, where, how serious, whether official sources confirm it, and what to watch for next.
Rules: copy numbers exactly as written (no "over" or "about"); do not add advice, instructions, causes, names or sources that are not in the draft.

Draft:
{report}

Answer in exactly this form:
SUBJECT: ...
ASSESSMENT: ..."""


def draft_assessment(report: str, endpoint: str, model: str, timeout: float = 120.0) -> tuple[str, str]:
    """(subject, assessment) suggested by the model for a follow-up report draft."""
    text = ollama_generate(endpoint, model, ASSESSMENT_PROMPT.format(report=report), timeout)
    subject, parts, in_assessment = "", [], False
    for raw in text.splitlines():
        line = raw.strip().strip("*").strip()   # models sometimes add **bold** labels
        label = line.split(":", 1)[0].strip("* ").upper() if ":" in line else ""
        if label == "SUBJECT":
            subject, in_assessment = line.split(":", 1)[1].strip(" *"), False
        elif label == "ASSESSMENT":
            in_assessment = True
            parts.append(line.split(":", 1)[1].strip(" *"))
        elif in_assessment and line:
            parts.append(line)   # the assessment can continue on the following lines
    assessment = " ".join(part for part in parts if part)
    if not assessment:
        raise OllamaError("Ollama's reply had no assessment")
    return subject, assessment
