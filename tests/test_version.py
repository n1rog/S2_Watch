import re
from pathlib import Path

from s2_watch import __version__


def test_version_is_semantic_and_set_in_one_place() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text()
    assert 'version = {attr = "s2_watch.__version__"}' in pyproject
    # No other hard-coded version strings.
    for path in (root / "s2_watch").rglob("*.py"):
        if path.name != "__init__.py":
            assert not re.search(r"""["']\d+\.\d+\.\d+["']""", path.read_text()), path
