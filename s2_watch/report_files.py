"""Where reports are saved: one reports folder, a subfolder per UTC day, every format at once.

Saving a draft writes the full report (text), its FLMSG and JS8Call versions, and for MAGNET
S2 reports the Word document and web page, under the report's own name
(``MAGNET_S2_TOPIC_YYMMDD-HHMMZ``), into ``<reports folder>/<YYYY-MM-DD>/``. Each file is
written under a temporary name and renamed when complete, so a half-written file never sits
under the real name.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

FOLDER_SETTING = "reports.folder"
LAST_DIRECTORY_SETTING = "reports.last_directory"     # where "Save … as" last saved


def default_folder() -> Path:
    """Documents/S2 Watch Reports (the home folder when there is no Documents folder)."""
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "S2 Watch Reports"


def reports_folder(store) -> Path:
    saved = store.get_setting(FOLDER_SETTING, "").strip()
    return Path(saved).expanduser() if saved else default_folder()


def day_folder(store, when: datetime | None = None) -> Path:
    when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return reports_folder(store) / when.strftime("%Y-%m-%d")


def write_atomic(path: Path, write) -> Path:
    """Write a file through ``write(temporary_path)``, then rename it into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.part{path.suffix}")
    try:
        write(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    return write_atomic(path, lambda temporary: Path(temporary).write_text(text, encoding=encoding,
                                                                            errors="replace"))


def save_all(folder: Path, stem: str, full: str, flmsg: str, js8call: str, magnet: bool) -> list[Path]:
    """Every format of one report in ``folder``; the files written."""
    written = [write_text(folder / f"{stem}.txt", full),
               write_text(folder / f"{stem}_FLMSG.txt", flmsg, "ascii"),
               write_text(folder / f"{stem}_JS8Call.txt", js8call, "ascii")]
    if magnet:
        from s2_watch.s2_docx import write_docx
        from s2_watch.s2_html import write_html
        from s2_watch.s2_report import parse_report

        report = parse_report(full)
        written.append(write_atomic(folder / f"{stem}.docx", lambda temporary: write_docx(report, temporary)))
        written.append(write_atomic(folder / f"{stem}.html", lambda temporary: write_html(report, temporary)))
    return written
