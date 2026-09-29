"""Build s2_watch/data/air_ambulance.json: aircraft registered to air-medical operators.

Many medical helicopters fly under their tail number (N123AB), not a LIFE/MEDEVAC-style
callsign, so the callsign alone misses them. This matches the owner/operator of every
aircraft in the open tar1090-db aircraft database (the one adsb.fi's "ownOp" comes from)
against air-medical operators and hospitals, keyed by ICAO transponder address.

Usage: python tools/build_air_ambulance.py   (re-run now and then; aircraft change hands)
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from s2_watch.overlays import USER_AGENT, is_air_medical_operator  # noqa: E402

SOURCE = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/csv/aircraft.csv.gz"
OUTPUT = ROOT / "s2_watch" / "data" / "air_ambulance.json"


def main() -> int:
    request = urllib.request.Request(SOURCE, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        text = gzip.decompress(response.read()).decode("utf-8", "replace")
    # hex;registration;type;flags;description;year;owner/operator;
    aircraft = {}
    for row in csv.reader(io.StringIO(text), delimiter=";"):
        if len(row) >= 7 and row[0] and is_air_medical_operator(row[6]):
            aircraft[row[0].lower()] = row[6].strip()
    OUTPUT.write_text(json.dumps(dict(sorted(aircraft.items())), separators=(",", ":")), encoding="utf-8")
    print(f"{OUTPUT}: {len(aircraft)} aircraft")
    return 0


if __name__ == "__main__":
    sys.exit(main())
