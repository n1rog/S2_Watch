"""The operator's focus: MARS regions, and optionally an area of responsibility (a radius
around a place). Something is in focus when it is in a chosen region or inside the area;
with neither chosen, everything is.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from s2_watch.silence import _distance_km


@dataclass(frozen=True)
class FocusArea:
    label: str
    lat: float
    lon: float
    km: int

    def contains(self, lat: float, lon: float) -> bool:
        return _distance_km(self.lat, self.lon, lat, lon) <= self.km

    def describe(self) -> str:
        return f"{self.km} km around {self.label}"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "FocusArea | None":
        try:
            data = json.loads(text) if text else None
            return cls(str(data["label"]), float(data["lat"]), float(data["lon"]), int(data["km"])) if data else None
        except (ValueError, KeyError, TypeError):
            return None


AREA: FocusArea | None = None


def set_area(area: FocusArea | None) -> None:
    global AREA
    AREA = area


def has_focus(regions) -> bool:
    return bool(regions) or AREA is not None


def point_in_focus(region: int | None, points, regions) -> bool:
    """A report or event (its MARS region and points) is in the focus: a chosen region, or
    inside the area. Not in focus when nothing is chosen (callers treat that as everything)."""
    if regions and region in regions:
        return True
    return AREA is not None and any(AREA.contains(lat, lon) for lat, lon in points or ())


def event_in_focus(event, regions) -> bool:
    return point_in_focus(event.region, event.points, regions)


def describe(regions) -> str:
    parts = [", ".join(f"MARS Region {r}" for r in sorted(regions))] if regions else []
    if AREA is not None:
        parts.append(AREA.describe())
    return " and ".join(parts)
