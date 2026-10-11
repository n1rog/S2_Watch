# Build with: pyinstaller --clean s2_watch.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPEC).parent
hiddenimports = collect_submodules("s2_watch")

a = Analysis(
    [str(project_root / "s2_watch" / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    # python-docx builds documents from its bundled Word templates.
    datas=[(str(project_root / "s2_watch" / "data"), "s2_watch/data")] + collect_data_files("docx"),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    name="s2-watch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    exclude_binaries=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="s2-watch",
)
