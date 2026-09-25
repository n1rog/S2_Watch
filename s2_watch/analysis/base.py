from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from s2_watch.models import Message


@dataclass(frozen=True)
class AnalysisResult:
    newsworthy: bool
    category: str
    urgency: str
    summary: str
    confidence: float
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


class NewsAnalyzer(ABC):
    @abstractmethod
    def analyze(self, message: Message) -> AnalysisResult:
        """Classify a normalized message."""
