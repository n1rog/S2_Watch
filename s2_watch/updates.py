"""Updates from the project's GitHub repository: newer version tags are offered, and installed
by downloading that version and running its own installer once S2 Watch has closed.

The installer can't replace S2 Watch while it runs, so a small script started on its own
waits for S2 Watch to exit, saves a copy of the database, runs ``installer/install.sh``, and
starts S2 Watch again, which then says whether the update worked (see ``update_outcome``).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from s2_watch import __version__

REPOSITORY = "n1rog/S2_Watch"
TAGS_URL = f"https://api.github.com/repos/{REPOSITORY}/tags?per_page=100"
COMPARE_URL = f"https://api.github.com/repos/{REPOSITORY}/compare/v{{old}}...v{{new}}"
ARCHIVE_URL = f"https://github.com/{REPOSITORY}/archive/refs/tags/v{{version}}.tar.gz"
USER_AGENT = f"S2-Watch/{__version__} (https://github.com/{REPOSITORY})"
CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# Runs on its own after S2 Watch closes: $1 S2 Watch's process id, $2 the downloaded version's
# folder, $3 the database, $4 the version, $5 S2 Watch's Python; the rest starts S2 Watch again.
UPDATER_SCRIPT = r"""#!/usr/bin/env bash
pid="$1"; source="$2"; database="$3"; version="$4"; python="$5"; shift 5
say() { command -v notify-send >/dev/null 2>&1 && notify-send -i s2-watch "S2 Watch" "$1" || true; }
echo "$(date -u +%FT%TZ) updating to $version from $source"
for _ in $(seq 1 240); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$pid" 2>/dev/null; then
    echo "S2 Watch did not close; update to $version cancelled"
    exit 1
fi
say "Installing S2 Watch $version… it reopens when done."
if [ -f "$database" ]; then
    mkdir -p "$(dirname "$database")/backups"
    "$python" -c 'import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); t = sqlite3.connect(sys.argv[2]); s.backup(t); t.close(); s.close()' \
        "$database" "$(dirname "$database")/backups/s2_watch-before-$version.db" \
        && echo "database copied to backups/s2_watch-before-$version.db"
fi
bash "$source/installer/install.sh" --yes --no-wizard < /dev/null
echo "installer finished with status $?"
exec "$@"
"""


def parse_version(text: str) -> tuple[int, int, int] | None:
    """(0, 41, 0) for the tag v0.41.0 or the version 0.41.0; None for anything else."""
    match = _TAG.match(text if text.startswith("v") else f"v{text}")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def _get_json(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def latest_version(timeout: float = 15) -> str:
    """The newest released version (such as 0.42.0), from the repository's version tags."""
    versions = [parse_version(tag.get("name", "")) for tag in _get_json(TAGS_URL, timeout)]
    versions = [version for version in versions if version]
    if not versions:
        raise ValueError("the repository has no version tags")
    return ".".join(str(part) for part in max(versions))


def newer(version: str, than: str = __version__) -> bool:
    new, old = parse_version(version), parse_version(than)
    return bool(new and old and new > old)


def changes(new: str, old: str = __version__, timeout: float = 15) -> list[str]:
    """The headline of each version between ``old`` and ``new`` ("Version 0.42.0: …"), newest
    first; empty when GitHub can't say."""
    try:
        commits = _get_json(COMPARE_URL.format(old=old, new=new), timeout).get("commits", [])
    except Exception:  # what's new is a courtesy: the update itself doesn't depend on it
        return []
    lines = [commit["commit"]["message"].splitlines()[0] for commit in commits]
    return [line for line in reversed(lines) if line.startswith("Version ")] or list(reversed(lines))


def install_kind() -> str:
    """How this S2 Watch was installed: "installer" (the installer's own environment, which an
    update replaces), "checkout" (a git clone: updated with git pull) or "other" (a build or a
    development copy, updated by hand)."""
    from s2_watch.checkout import editable_checkout

    if editable_checkout():
        return "checkout"
    package = Path(__file__).resolve()
    if sys.platform != "win32" and Path(sys.prefix).name == "venv" and Path(sys.prefix).resolve() in package.parents:
        return "installer"
    return "other"


def download(version: str, folder: Path | None = None, timeout: float = 60) -> Path:
    """Download and unpack ``version``; return its folder (the one holding installer/install.sh)."""
    folder = Path(folder or tempfile.mkdtemp(prefix="s2-watch-update-"))
    archive = folder / f"S2_Watch-{version}.tar.gz"
    request = urllib.request.Request(ARCHIVE_URL.format(version=version), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, archive.open("wb") as out:
        while chunk := response.read(1 << 16):
            out.write(chunk)
    return unpack(archive, version, folder)


def unpack(archive: Path, version: str, folder: Path) -> Path:
    with tarfile.open(archive) as tar:
        tar.extractall(folder / "source", filter="data")
    tops = [path for path in (folder / "source").iterdir() if path.is_dir()]
    if len(tops) != 1 or not (tops[0] / "installer/install.sh").is_file():
        raise ValueError("the download isn't an S2 Watch release (no installer in it)")
    found = re.search(r'^__version__ = "(.*)"', (tops[0] / "s2_watch/__init__.py").read_text(encoding="utf-8"), re.M)
    if not found or found.group(1) != version:
        raise ValueError(f"the download is version {found.group(1) if found else 'unknown'}, not {version}")
    return tops[0]


def start_updater(source: Path, database: Path, version: str, pid: int, log: Path,
                  relaunch: list[str] | None = None) -> subprocess.Popen:
    """Start the script that installs ``source`` once process ``pid`` (S2 Watch) has exited."""
    script = source.parent.parent / "update.sh"
    script.write_text(UPDATER_SCRIPT, encoding="utf-8")
    relaunch = relaunch or [sys.executable, "-m", "s2_watch.main", *sys.argv[1:]]
    with log.open("a", encoding="utf-8") as output:
        return subprocess.Popen(
            ["bash", str(script), str(pid), str(source), str(database), version, sys.executable, *relaunch],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)


def update_outcome(pending: str | None, running: str = __version__) -> str | None:
    """After an update S2 Watch reopens: "updated" when it now runs the version it went to
    install, "failed" when it doesn't, None when no update was under way."""
    if not pending:
        return None
    return "updated" if running == pending else "failed"
