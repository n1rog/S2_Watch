"""Build the S2 Watch installer package: dist/S2Watch-<version>-installer.zip.

The zip holds the install/uninstall scripts for Linux, macOS and Windows plus the program
source in app/, so it installs without access to the (private) GitHub repository.

Usage: python tools/make_installer_package.py
"""

from __future__ import annotations

import re
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_FILES = ("install.sh", "uninstall.sh", "install.ps1", "install.cmd", "uninstall.ps1", "README-INSTALL.txt")
APP_FILES = ("pyproject.toml", "README.md")


def version() -> str:
    text = (ROOT / "s2_watch" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "(.*)"', text, re.MULTILINE).group(1)


def add(archive: zipfile.ZipFile, source: Path, name: str) -> None:
    info = zipfile.ZipInfo.from_file(source, name)
    mode = 0o755 if source.suffix == ".sh" else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    data = source.read_bytes()
    if source.suffix in {".cmd", ".ps1"} or source.name == "README-INSTALL.txt":
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # Windows line endings
    archive.writestr(info, data)


def main() -> int:
    top = f"S2Watch-{version()}-installer"
    output = ROOT / "dist" / f"{top}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for name in INSTALLER_FILES:
            add(archive, ROOT / "installer" / name, f"{top}/{name}")
        for name in APP_FILES:
            add(archive, ROOT / name, f"{top}/app/{name}")
        for source in sorted((ROOT / "s2_watch").rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                add(archive, source, f"{top}/app/{source.relative_to(ROOT).as_posix()}")
    print(f"{output} ({output.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
