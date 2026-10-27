"""Analysis with a local Ollama model, used together with the keyword rules.

The rules run first and decide quickly which messages are plain traffic; the model reads
only the messages the rules flag, where understanding matters (negations such as "NO
CHANGE: TERRORISM", paraphrases, ham shorthand). If Ollama is slow, stopped or returns
something unusable, the rules' result stands. Nothing leaves the computer.
"""

import json
import re
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
{hint}{examples}
Answer with JSON only: {{"newsworthy": true|false, "category": "...", "urgency": "low|medium|high", "summary": "...", "confidence": 0.0, "matched_terms": ["..."]}}

Message: {message}"""


# Sender and addressee are callsigns (they contain a digit) or @groups; a capitalised
# first word of the message ("W5ABC: BRIDGE OUT ...") is not an addressee.
JS8_ROUTING = re.compile(r"^[A-Z0-9/]*\d[A-Z0-9/]*:\s+(?:(?:@[A-Z0-9/]+|[A-Z0-9/]*\d[A-Z0-9/]*)\s+)?(?:MSG\s+|>\s*)?")


def readable_text(message: Message) -> str:
    """The words of a message: JS8Call messages arrive as its API record (CMD, DIAL, FREQ,
    SNR, TEXT...), and the numbers confuse a small model, so only the TEXT is given."""
    text = message.raw_content.strip()
    if text.startswith("{"):
        try:
            record = json.loads(text)
        except ValueError:
            return text
        if isinstance(record, dict):
            words = record.get("TEXT") or record.get("VALUE") or record.get("text")
            if isinstance(words, str) and words.strip():
                text = words.replace("\u2662", "").strip()   # JS8Call's end-of-message mark
    # "W0IFM: KL5OP MSG ..." / "N6KYL: @MR09 MSG ...": sender, addressee and command are
    # routing; the body is what the model should judge.
    return JS8_ROUTING.sub("", text, count=1) or text


CATEGORY_ALIASES = {
    "weather": "Climate / Environment", "climate": "Climate / Environment", "environment": "Climate / Environment",
    "natural disaster": "Climate / Environment", "disaster": "Climate / Environment",
    "fire": "Climate / Environment", "wildfire": "Climate / Environment",
    "accident": "Infrastructure", "traffic": "Infrastructure", "transportation": "Infrastructure",
    "power": "Infrastructure", "utilities": "Infrastructure", "communications": "Infrastructure",
    "health": "Health / Wellness", "medical": "Health / Wellness", "public health": "Health / Wellness",
    "unrest": "Civil Unrest", "protest": "Civil Unrest", "protests": "Civil Unrest",
    "military": "Military Conflict", "war": "Military Conflict", "security": "Terrorism",
    "economic": "Economy", "finance": "Economy", "supply": "Supply Chain", "logistics": "Supply Chain",
    "government": "Politics", "political": "Politics", "law enforcement": "Crime",
}


class OllamaError(RuntimeError):
    """Raised when Ollama cannot provide a valid analysis."""


def ollama_generate(endpoint: str, model: str, prompt: str, timeout: float, json_format: bool = False,
                    options: dict | None = None, keep_alive=None) -> str:
    """One completion from Ollama's /api/generate; raises OllamaError when it can't. ``options``
    adds model options (num_predict, repeat_penalty, stop...)."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OllamaError(f"Invalid Ollama endpoint: {endpoint}")
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False,
                               "keep_alive": KEEP_ALIVE if keep_alive is None else keep_alive,
                               "options": {"temperature": 0, **(options or {})}}
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
    if response.status == 404:
        raise OllamaError(f"Ollama returned HTTP 404: model {model} isn't installed (ollama pull {model})")
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
        # The watch officer's recent decisions on possible misses: (message, verdict) pairs,
        # verdict like "news (Climate / Environment)" or "not news".
        self.examples: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def analyze(self, message: Message, hint: AnalysisResult | None = None) -> AnalysisResult:
        suggestion = ""
        if hint is not None and hint.newsworthy:
            suggestion = (f'A keyword filter matched {", ".join(hint.matched_terms[:6]) or "some words"} and suggested '
                          f'"{hint.category}"; keywords can be wrong (negations, unrelated uses), so decide yourself.')
        examples = ""
        if self.examples:
            examples = "\nHow the watch officer judged similar messages:\n" + "\n".join(
                f'- "{text[:160]}" -> {verdict}' for text, verdict in self.examples[:8]) + "\n"
        prompt = PROMPT.format(categories=", ".join(NEWS_CATEGORIES), hint=suggestion, examples=examples,
                               message=readable_text(message))
        text = ollama_generate(self.endpoint, self.model, prompt, self.timeout, json_format=True)
        try:
            result = self._validate_result(json.loads(text))
        except (TypeError, ValueError) as error:
            raise OllamaError("Ollama returned invalid analysis JSON") from error
        if result.summary.strip().lower() in {"", "none", "n/a", "null"}:
            # No headline from the model: S2 Watch writes its own from the message.
            result = AnalysisResult(result.newsworthy, result.category, result.urgency, message.raw_content,
                                    result.confidence, result.matched_terms)
        return result

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
        elif isinstance(category, str) and category not in NEWS_CATEGORIES:
            # Small models sometimes answer with a near-synonym of a category.
            category = CATEGORY_ALIASES.get(category.strip().lower(), category)
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


BRIEFING_PROMPT = """You help an amateur-radio emergency-net operator write the assessment for a shift briefing.
Below is their draft briefing. Write ASSESSMENT: three to five short sentences: the overall situation, the most
significant items and why, what official sources confirm, and what to watch during the next shift.
Rules: use only facts in the draft; copy numbers exactly; do not add advice, causes, names or sources.

Draft:
{report}

Answer in exactly this form:
ASSESSMENT: ..."""


def draft_briefing_assessment(report: str, endpoint: str, model: str, timeout: float = 180.0) -> tuple[str, str]:
    """("", assessment) suggested by the model for a briefing draft (no subject line)."""
    text = ollama_generate(endpoint, model, BRIEFING_PROMPT.format(report=report), timeout)
    parts, in_assessment = [], False
    for raw in text.splitlines():
        line = raw.strip().strip("*").strip()
        if line.split(":", 1)[0].strip("* ").upper() == "ASSESSMENT" and ":" in line:
            in_assessment = True
            parts.append(line.split(":", 1)[1].strip(" *"))
        elif in_assessment and line:
            parts.append(line)
    assessment = " ".join(part for part in parts if part) or text.strip()
    if not assessment:
        raise OllamaError("Ollama's reply had no assessment")
    return "", assessment


S2_ASSESSMENT_PROMPT = """You help a MAGNET S2 (amateur-radio emergency network intelligence) analyst finish a report.
Below is the draft. Write two parts, using ONLY facts in the draft:
COMMENTS / ASSESSMENT: two to four short paragraphs of analysis in measured language (assessed, likely,
indicates, suggests), the most significant indicator first. No new facts, numbers, names or sources.
INTELLIGENCE GAPS: three to five open questions the reporting leaves unanswered, one per line starting with "- ".

Draft:
{report}

Answer in exactly this form:
COMMENTS / ASSESSMENT:
...
INTELLIGENCE GAPS:
- ..."""


def draft_s2_assessment(report: str, endpoint: str, model: str, timeout: float = 240.0) -> str:
    """The draft report with the model's assessment and gaps filled in (the analyst edits them)."""
    from s2_watch.s2_report import fill_assessment

    text = ollama_generate(endpoint, model, S2_ASSESSMENT_PROMPT.format(report=report), timeout)
    paragraphs, gaps, part = [], [], ""
    for raw in text.splitlines():
        line = raw.strip().strip("*").strip()
        heading = line.rstrip(":").strip("* ").upper()
        if heading.startswith("COMMENTS"):
            part = "assessment"
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest:
                paragraphs.append(rest)
        elif heading.startswith("INTELLIGENCE GAPS"):
            part = "gaps"
        elif part == "assessment" and line:
            paragraphs.append(line)
        elif part == "gaps" and line.startswith(("-", "•", "*")):
            gaps.append(line.lstrip("-•* ").strip())
    if not paragraphs:
        raise OllamaError("Ollama's reply had no assessment")
    return fill_assessment(report, paragraphs, gaps)
