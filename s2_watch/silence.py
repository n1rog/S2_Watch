"""Radio silence as a signal: grid squares whose stations have gone quiet near an event.

A square counts as quiet when, at the rate its stations were heard over the review window,
at least EXPECTED_MESSAGES would have arrived since the last one, and at least QUIET_AFTER
has passed. On its own that is often just a lull (a net ended, night); it matters next to
an open event, which is the only place S2 Watch reports it.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

QUIET_AFTER = timedelta(hours=2)
EXPECTED_MESSAGES = 4       # what the usual rate would have brought since the last message
MIN_MESSAGES = 8            # fewer heard in the window: no usual rate to compare with
MIN_SPAN = timedelta(hours=3)
NEAR_KM = 150


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def quiet_cells(cells: list[dict], now: datetime) -> list[dict]:
    """The coverage cells (CoverageTracker.snapshot) that have gone quiet, each with
    "silent_hours" and "usual_per_3h" added."""
    quiet = []
    for cell in cells:
        first, last = datetime.fromisoformat(cell["first"]), datetime.fromisoformat(cell["last"])
        span, silent = last - first, now - last
        if cell["messages"] < MIN_MESSAGES or span < MIN_SPAN or silent < QUIET_AFTER:
            continue
        per_hour = cell["messages"] / (span.total_seconds() / 3600)
        if per_hour * silent.total_seconds() / 3600 >= EXPECTED_MESSAGES:
            quiet.append({**cell, "silent_hours": int(silent.total_seconds() // 3600),
                          "usual_per_3h": round(per_hour * 3)})
    return quiet


def cell_centre(cell: dict) -> tuple[float, float]:
    return cell["south"] + 0.5, cell["west"] + 1.0


def quiet_near(event, cells: list[dict], now: datetime, region_of=None) -> list[dict]:
    """Quiet cells near an open event: within NEAR_KM of its points, or (no points) in its
    MARS region (``region_of(lat, lon)``)."""
    if event.closed_at is not None:
        return []
    found = []
    for cell in quiet_cells(cells, now):
        lat, lon = cell_centre(cell)
        if event.points:
            near = any(_distance_km(lat, lon, plat, plon) <= NEAR_KM for plat, plon in event.points)
        else:
            near = bool(event.region and region_of and region_of(lat, lon) == event.region)
        if near:
            found.append(cell)
    return sorted(found, key=lambda cell: -cell["usual_per_3h"])
