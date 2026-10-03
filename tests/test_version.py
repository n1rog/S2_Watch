import re
from pathlib import Path

from s2_watch import __version__


def test_version_is_semantic_and_set_in_one_place() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = {attr = "s2_watch.__version__"}' in pyproject
    # No other hard-coded version strings.
    for path in (root / "s2_watch").rglob("*.py"):
        if path.name != "__init__.py":
            assert not re.search(r"""["']\d+\.\d+\.\d+["']""", path.read_text(encoding="utf-8")), path


def test_software_map_rendering_setting_disables_the_gpu_before_qt_starts(tmp_path, monkeypatch) -> None:
    import os

    from s2_watch.main import apply_map_rendering
    from s2_watch.storage import SQLiteStore

    database = tmp_path / "s2.db"
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--foo")
    apply_map_rendering(str(tmp_path / "missing.db"))           # no database: nothing changes
    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--foo" and not (tmp_path / "missing.db").exists()
    store = SQLiteStore(database)
    store.set_setting("map.rendering", "hardware")
    apply_map_rendering(str(database))
    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--foo"
    store.set_setting("map.rendering", "software")
    apply_map_rendering(str(database))
    apply_map_rendering(str(database))                          # added once
    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--foo --disable-gpu"
    store.close()
