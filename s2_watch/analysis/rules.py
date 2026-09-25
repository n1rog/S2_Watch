import re
from dataclasses import dataclass
from functools import cached_property

from s2_watch.analysis.base import AnalysisResult, NewsAnalyzer
from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS, ENVIRONMENTAL_HAZARDS_PATTERN
from s2_watch.models import Message


@dataclass(frozen=True)
class Rule:
    """Terms match at the start of a word ("flood" matches "flooding", "riot" not "patriot").

    Patterns are regular expressions for phrasing that plain terms cannot cover.
    """

    category: str
    terms: tuple[str, ...]
    urgency: str = "medium"
    patterns: tuple[str, ...] = ()

    @cached_property
    def _compiled(self) -> tuple[re.Pattern, ...]:
        return tuple(
            [re.compile(r"\b" + re.escape(term.casefold())) for term in self.terms]
            + [re.compile(pattern, re.IGNORECASE) for pattern in self.patterns]
        )

    def matches(self, content: str) -> list[str]:
        found = []
        for pattern in self._compiled:
            match = pattern.search(content)
            if match:
                found.append(match.group(0).casefold())
        return found


# Heat, cold and winter weather; readings alone ("20F GUD HERE") are not news.
# Civil unrest. "Demonstration" alone is left out: it is mostly "for demonstration purposes".
SEVERE_UNREST_PATTERNS = (
    r"\briot(?:s|ing|ers?)?\b", r"\bloot(?:ing|ers?|ed)\b", r"\bcurfews?\b", r"\bcivil\s+(?:unrest|disorder)\b",
    r"\b(?:tear\s*gas(?:sed)?|rubber\s+bullets?|pepper\s*spray(?:ed)?|flash\s*bangs?|less[-\s]lethal)\b",
    r"\b(?:unlawful\s+assembl(?:y|ies)|dispersal\s+orders?)\b", r"\binsurrection(?:s|ists?)?\b",
    r"\bnational\s+guard\s+(?:deployed|deploying|activated|called\s+(?:in|up|out)|mobiliz\w+|troops)\b",
)
UNREST_PATTERNS = (
    r"\b(?:counter[-\s]?|anti[-\s]?)?protest(?:s|ers?|ors?|ing|ed)?\b", r"\bdemonstrat(?:ions|ors?)\b",
    r"\bunrest\b", r"\bcivil\s+(?:disturbances?|tensions?)\b", r"\bmarchers\b",
    r"\brall(?:y|ies)\b(?!\s+points?)", r"\bno\s+kings\b", r"\b(?:angry|violent|hostile|unruly)\s+crowds?\b",
    r"\bblocking\s+(?:roads?|streets?|highways?|traffic|intersections?|freeways?)\b",
)

SEVERE_WEATHER_PATTERNS = (
    r"\b(?:excessive|extreme|dangerous)\s+heat\b",
    r"\bheat\s*(?:warnings?|emergenc(?:y|ies))\b",
    r"\b(?:bitter(?:ly)?|extreme|dangerous)\s+cold\b",
    r"\bwind\s*chill\s+warnings?\b",
    r"\b(?:blizzards?|ice\s*storms?|derechos?|tropical\s+storms?)\b",
)
WEATHER_PATTERNS = (
    r"\b(?:high|intense|oppressive)\s+heat\b",
    r"\bheat\s*(?:advisor(?:y|ies)|index|indices|waves?|domes?|exhaustion|strokes?|illness(?:es)?)\b",
    r"\brecord\s+(?:high|low|heat|cold)s?\b", r"\btriple[-\s]digits?\b",
    r"\bsevere\s+cold\b",
    r"\bcold\s+(?:waves?|snaps?|advisor(?:y|ies)|warnings?|weather\s+advisor(?:y|ies))\b",
    r"\bwind\s*chills?\b", r"\b(?:hard\s+)?freeze\s+(?:warnings?|watch(?:es)?)\b", r"\bhard\s+freeze\b",
    r"\bfrost\s+advisor(?:y|ies)\b", r"\b(?:sub|below)[-\s]?(?:freezing|zero)\b",
    r"\barctic\s+(?:blast|air|outbreak)\b", r"\bpolar\s+vortex\b", r"\bhypotherm", r"\bfrostbite\b",
    r"\bwinter\s+(?:storms?|weather\s+advisor(?:y|ies))\b", r"\bsnow\s*(?:fall|storms?|squalls?|emergenc(?:y|ies))\b",
    r"(?<![!#\w.])\d+(?:-\d+)?\s*(?:\"|in(?:ch(?:es)?)?\.?)\s+(?:of\s+)?snow\b",
    r"\b(?:icy\s+(?:roads?|conditions|streets?|highways?)|black\s+ice|freezing\s+rain|sleet)\b",
    r"\b(?:thunder\s*storms?|hail(?:storms?)?|lightning|severe\s+weather|high\s+winds?|damaging\s+winds?)\b",
    r"\b(?:red\s+flag\s+warnings?|droughts?|dense\s+fog|heavy\s+rain|dust\s*storms?)\b",
)


class RuleBasedNewsAnalyzer(NewsAnalyzer):
    """Deterministic, offline classifier intended as a safe baseline."""

    DEFAULT_RULES = (
        Rule("Climate / Environment", ("tornado", "hurricane", "flood", "evacuation", "wildfire", "earthquake"), "high",
             SEVERE_WEATHER_PATTERNS),
        Rule("Climate / Environment", (), "medium", WEATHER_PATTERNS),
        Rule("Crime", ("missing person", "burglary", "robbery", "arrest", "murder", "shooting", "shots fired",
                       "stabbing", "homicide", "active shooter"), "high"),
        Rule("Infrastructure", ("power outage", "bridge closed", "road closed", "water main", "communications outage"), "medium"),
        Rule("Health / Wellness", ("medical emergency", "ambulance", "injured", "outbreak", "hospital"), "high"),
        Rule("Civil Unrest", (), "high", SEVERE_UNREST_PATTERNS),
        Rule("Civil Unrest", (), "medium", UNREST_PATTERNS),
        Rule("Economy", ("bank failure", "market crash", "inflation", "unemployment"), "medium"),
        Rule("Military Conflict", ("military conflict", "airstrike", "invasion", "combat", "ceasefire"), "high"),
        Rule("Politics", ("election", "legislation", "government", "political"), "medium"),
        Rule("Supply Chain", ("shortage", "supply chain", "shipment delay", "port closure"), "medium"),
        Rule("Terrorism", ("terrorist", "terrorism", "bomb threat", "extremist attack"), "high"),
        # The category watch forms' vocabulary: whatever a category form captures is at
        # least medium-urgency news in that category.
        *(Rule(category, (), "medium", (pattern,)) for category, pattern in CATEGORY_PATTERNS.items()
          if category != "Climate / Environment"),
        Rule("Climate / Environment", (), "medium", (ENVIRONMENTAL_HAZARDS_PATTERN,)),
    )

    def __init__(self, rules: tuple[Rule, ...] = DEFAULT_RULES) -> None:
        self.rules = rules

    def analyze(self, message: Message) -> AnalysisResult:
        structured = " ".join(
            value for key, value in message.fields.items()
            if key in {"type", "severity", "scope", "remarks", "grid", "recipient"}
        )
        content = f"{message.raw_content} {structured}".casefold()
        matches = [
            (rule, term)
            for rule in self.rules
            for term in rule.matches(content)
        ]
        if not matches:
            return AnalysisResult(False, "routine", "low", message.raw_content, 0.1)
        rule_by_priority = {"high": 3, "medium": 2, "low": 1}
        rule, _ = max(matches, key=lambda item: rule_by_priority[item[0].urgency])
        terms = tuple(dict.fromkeys(term for _, term in matches))
        confidence = min(0.95, 0.55 + 0.1 * len(terms) + (0.1 if message.fields else 0))
        summary = message.raw_content
        if message.fields.get("type") == "ALERTS" and message.fields.get("severity"):
            summary = f"MagCommstat alert (severity {message.fields['severity']}): {summary}"
        elif message.fields.get("type") == "STATREP" and message.fields.get("scope"):
            summary = f"StatRep for {message.fields['scope']}: {summary}"
        return AnalysisResult(
            True,
            rule.category,
            rule.urgency,
            summary,
            confidence,
            terms,
        )
