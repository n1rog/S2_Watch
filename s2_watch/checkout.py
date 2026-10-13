"""Installs made from a git clone run the clone's code (pip install -e), so ``git pull`` updates
S2 Watch. A pull can also change the packages S2 Watch needs; at startup, before Qt loads,
those are installed when the clone's list differs from the one last installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

CHECKOUT = Path(__file__).resolve().parent.parent
STAMP_NAME = "s2-watch-requirements.txt"   # in the environment: the requirements last installed


def editable_checkout(checkout: Path = CHECKOUT) -> bool:
    """True when this environment's S2 Watch is an editable install of ``checkout``, a git clone."""
    if not (checkout / ".git").exists() or not (checkout / "pyproject.toml").is_file():
        return False
    # pip leaves s2_watch.egg-info in the clone, which Python also finds when started there;
    # only the environment's record says how S2 Watch was installed.
    for distribution in metadata.distributions(name="s2-watch"):
        try:
            info = json.loads(distribution.read_text("direct_url.json") or "{}")
        except ValueError:
            continue
        url = urlparse(info.get("url", ""))
        if info.get("dir_info", {}).get("editable") and url.scheme == "file" and \
                Path(url2pathname(url.path)).resolve() == checkout.resolve():
            return True
    return False


def requirements(checkout: Path = CHECKOUT) -> str:
    project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return "\n".join(project.get("dependencies", [])) + "\n"


def record_requirements(checkout: Path = CHECKOUT, prefix: str = sys.prefix) -> None:
    """After installing: remember which requirements this environment has."""
    if editable_checkout(checkout):
        (Path(prefix) / STAMP_NAME).write_text(requirements(checkout), encoding="utf-8")


def refresh_packages_after_pull(checkout: Path = CHECKOUT, prefix: str = sys.prefix, run=subprocess.run) -> bool:
    """Install the packages a pulled version needs. Returns True when pip ran and succeeded."""
    if not editable_checkout(checkout):
        return False
    stamp = Path(prefix) / STAMP_NAME
    wanted = requirements(checkout)
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == wanted:
        return False
    python = Path(sys.executable)
    options = {}
    if sys.platform == "win32":
        # Started from the Start menu with pythonw: show pip's progress in a console window.
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        options["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    print("S2 Watch: installing the packages this version needs...", file=sys.stderr)
    result = run([str(python), "-m", "pip", "install", "--upgrade", "-e", str(checkout)], **options)
    if result.returncode != 0:
        print("S2 Watch: installing packages failed; run the installer in the clone's installer folder again.",
              file=sys.stderr)
        return False
    stamp.write_text(wanted, encoding="utf-8")
    return True
