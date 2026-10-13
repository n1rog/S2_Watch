import json
from pathlib import Path
from types import SimpleNamespace

import s2_watch.checkout as checkout


def make_clone(tmp_path: Path, dependencies: str) -> Path:
    clone = tmp_path / "S2 Watch clone"
    (clone / ".git").mkdir(parents=True, exist_ok=True)
    (clone / "pyproject.toml").write_text(f"[project]\nname = 's2-watch'\ndependencies = [{dependencies}]\n",
                                          encoding="utf-8")
    return clone


def installed_from(monkeypatch, clone: Path, editable: bool = True) -> None:
    info = {"url": clone.resolve().as_uri(), "dir_info": {"editable": editable}}
    clone_egg_info = SimpleNamespace(read_text=lambda _file: None)     # found first when started in the clone
    environment = SimpleNamespace(read_text=lambda _file: json.dumps(info))
    monkeypatch.setattr(checkout.metadata, "distributions", lambda name: iter([clone_egg_info, environment]))


def test_a_pull_that_changes_the_packages_installs_them_once(tmp_path, monkeypatch) -> None:
    clone = make_clone(tmp_path, "'PySide6>=6.10,<6.12'")
    prefix = tmp_path / "venv"
    prefix.mkdir()
    installed_from(monkeypatch, clone)
    checkout.record_requirements(clone, str(prefix))          # what the installer does
    calls = []
    run = lambda command, **_options: calls.append(command) or SimpleNamespace(returncode=0)
    assert not checkout.refresh_packages_after_pull(clone, str(prefix), run)     # nothing new pulled
    make_clone(tmp_path, "'PySide6>=6.10,<6.13', 'python-docx>=1.1,<2'")         # git pull
    assert checkout.refresh_packages_after_pull(clone, str(prefix), run)
    assert calls[0][-2:] == ["-e", str(clone)]
    assert not checkout.refresh_packages_after_pull(clone, str(prefix), run)     # done, not again
    assert len(calls) == 1


def test_a_failed_install_is_tried_again_next_start(tmp_path, monkeypatch) -> None:
    clone = make_clone(tmp_path, "'PySide6'")
    installed_from(monkeypatch, clone)
    failing = lambda command, **_options: SimpleNamespace(returncode=1)
    assert not checkout.refresh_packages_after_pull(clone, str(tmp_path), failing)
    assert not (tmp_path / checkout.STAMP_NAME).exists()


def test_installs_from_the_package_are_left_alone(tmp_path, monkeypatch) -> None:
    clone = make_clone(tmp_path, "'PySide6'")
    installed_from(monkeypatch, clone, editable=False)        # the release zip: a copy, not the clone
    ran = lambda command, **_options: (_ for _ in ()).throw(AssertionError("pip ran"))
    assert not checkout.refresh_packages_after_pull(clone, str(tmp_path), ran)
    (clone / ".git").rmdir()
    installed_from(monkeypatch, clone)                       # editable, but not a git clone
    assert not checkout.refresh_packages_after_pull(clone, str(tmp_path), ran)
