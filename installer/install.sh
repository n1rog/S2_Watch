#!/usr/bin/env bash
# S2 Watch installer for Linux and macOS.
#
# Checks for and installs everything S2 Watch needs (Python 3.11+, the system libraries
# Qt needs on Linux, and the Python packages), installs S2 Watch into its own Python
# environment, creates menu/desktop launchers, then walks you through configuration.
# Safe to run again: it upgrades the program and keeps your data and settings.
#
# Usage: ./install.sh [--yes] [--no-wizard]
#   --yes        accept the default answer to the installer's own questions
#   --no-wizard  skip the configuration wizard (run it later with: s2-watch --configure)

set -euo pipefail

ASSUME_YES=0
RUN_WIZARD=1
for argument in "$@"; do
    case "$argument" in
        --yes|-y) ASSUME_YES=1 ;;
        --no-wizard) RUN_WIZARD=0 ;;
        --help|-h) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "Unknown option: $argument (try --help)"; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/app/pyproject.toml" ]; then
    SOURCE_DIR="$SCRIPT_DIR/app"            # installer package
elif [ -f "$SCRIPT_DIR/../pyproject.toml" ]; then
    SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # run from a checkout of the project
else
    echo "Cannot find the S2 Watch program files next to this installer." >&2
    exit 1
fi
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SOURCE_DIR/s2_watch/__init__.py")"
OS="$(uname -s)"
MIN_PYTHON_MINOR=11

if [ "$OS" = "Darwin" ]; then
    PREFIX="$HOME/Library/Application Support/S2 Watch"
    DATA="$PREFIX/s2_watch.db"
else
    PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/s2-watch"
    DATA="$PREFIX/s2_watch.db"
fi
VENV="$PREFIX/venv"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mInstallation stopped: %s\033[0m\n' "$*" >&2; exit 1; }
confirm() {  # confirm "question" default(y|n)
    local default="${2:-y}" answer
    if [ "$ASSUME_YES" = 1 ]; then [ "$default" = y ]; return; fi
    read -r -p "  $1 [$( [ "$default" = y ] && echo Y/n || echo y/N )] " answer || answer=""
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

bold "S2 Watch $VERSION installer ($OS)"
info "Program:  $VENV"
info "Data:     $DATA"

# ---- 1. Internet --------------------------------------------------------------------------
bold "1/7  Checking the internet connection"
if curl -fsS --max-time 15 -o /dev/null https://pypi.org/simple/pyside6/ 2>/dev/null; then
    info "OK: can reach the Python package index."
else
    fail "cannot reach https://pypi.org. The installer downloads Python packages (~400 MB); connect and try again."
fi

# ---- 2. Python 3.11+ ----------------------------------------------------------------------
bold "2/7  Checking for Python 3.$MIN_PYTHON_MINOR or newer"
python_ok() {  # python_ok command -> prints its path if it is 3.11+ with venv
    "$1" - <<'PY' 2>/dev/null
import sys, venv, ensurepip
assert sys.version_info >= (3, 11)
print(sys.executable)
PY
}
find_python() {
    local candidate
    for candidate in python3.13 python3.12 python3.11 python3 \
                     /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                     /Library/Frameworks/Python.framework/Versions/3.1[1-9]/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
            python_ok "$candidate" && return 0
        fi
    done
    return 1
}
PYTHON="$(find_python || true)"

install_python() {
    if [ "$OS" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            info "Installing Python 3.12 with Homebrew..."
            brew install python@3.12
        else
            local package="python-3.12.7-macos11.pkg" url
            url="https://www.python.org/ftp/python/3.12.7/$package"
            info "Downloading Python 3.12 from python.org..."
            curl -fL --progress-bar -o "/tmp/$package" "$url"
            info "Installing it (macOS will ask for your password)..."
            sudo installer -pkg "/tmp/$package" -target /
            rm -f "/tmp/$package"
        fi
        return
    fi
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        local version
        for version in 3.13 3.12 3.11; do
            if grep -q 'Candidate: [0-9]' <<< "$(apt-cache policy "python$version" 2>/dev/null)"; then
                sudo apt-get install -y "python$version" "python$version-venv"
                return
            fi
        done
        fail "no Python 3.11+ package is available for this system. Install Python 3.11 or newer, then run the installer again."
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3.12 || sudo dnf install -y python3
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm python
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y python312 || sudo zypper install -y python311
    else
        fail "no supported package manager found. Install Python 3.11 or newer, then run the installer again."
    fi
}

if [ -n "$PYTHON" ]; then
    info "OK: $("$PYTHON" --version) ($PYTHON)"
else
    warn "Python 3.$MIN_PYTHON_MINOR or newer (with its 'venv' module) was not found."
    if [ "$OS" = "Linux" ] && command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; exit(sys.version_info < (3, 11))' 2>/dev/null; then
        info "Python $(python3 --version 2>&1 | cut -d' ' -f2) is present but its 'venv' module is missing."
    fi
    confirm "Install Python now (uses your system's package manager; may ask for your password)?" y \
        || fail "Python 3.11+ is required."
    install_python
    hash -r
    PYTHON="$(find_python || true)"
    [ -n "$PYTHON" ] || fail "Python was installed but could not be found. Open a new terminal and run the installer again."
    info "OK: $("$PYTHON" --version) ($PYTHON)"
fi

# ---- 3. System libraries (Linux) ----------------------------------------------------------
bold "3/7  Checking the system libraries S2 Watch's window and map need"
if [ "$OS" = "Linux" ]; then
    missing=()
    # Read the list once: piping it into 'grep -q' under pipefail fails whenever grep stops
    # early, which made present libraries look missing.
    libraries="$(ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || true)"
    for library in libxcb-cursor.so.0 libnss3.so libxkbcommon-x11.so.0 libEGL.so.1 libGL.so.1 libasound.so.2 \
                   libfontconfig.so.1 libdbus-1.so.3 libxcb-icccm.so.4 libxcb-keysyms.so.1 libxcb-image.so.0 \
                   libxcb-render-util.so.0 libxcb-shape.so.0 libXcomposite.so.1 libXdamage.so.1 libXrandr.so.2 \
                   libXtst.so.6 libxkbfile.so.1; do
        grep -qF "$library" <<< "$libraries" || missing+=("$library")
    done
    if [ ${#missing[@]} -eq 0 ]; then
        info "OK: all present."
    else
        warn "Missing: ${missing[*]}"
        packages=()
        if command -v apt-get >/dev/null 2>&1; then
            asound=libasound2
            grep -q 'Candidate: [0-9]' <<< "$(apt-cache policy libasound2t64 2>/dev/null)" && asound=libasound2t64
            packages=(libxcb-cursor0 libnss3 libxkbcommon-x11-0 libegl1 libgl1 "$asound" libfontconfig1 libdbus-1-3
                      libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxcb-shape0 libxcomposite1
                      libxdamage1 libxrandr2 libxtst6 libxkbfile1)
            install_command=(sudo sh -c 'apt-get update -qq && apt-get install -y "$@"' apt-get)
        elif command -v dnf >/dev/null 2>&1; then
            packages=(xcb-util-cursor nss libxkbcommon-x11 mesa-libEGL mesa-libGL alsa-lib fontconfig dbus-libs
                      xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil libXcomposite libXdamage
                      libXrandr libXtst libxkbfile)
            install_command=(sudo dnf install -y)
        elif command -v pacman >/dev/null 2>&1; then
            packages=(xcb-util-cursor nss libxkbcommon-x11 libglvnd alsa-lib fontconfig dbus xcb-util-wm
                      xcb-util-keysyms xcb-util-image xcb-util-renderutil libxcomposite libxdamage libxrandr libxtst libxkbfile)
            install_command=(sudo pacman -S --needed --noconfirm)
        elif command -v zypper >/dev/null 2>&1; then
            packages=(libxcb-cursor0 mozilla-nss libxkbcommon-x11-0 libEGL1 libGL1 libasound2 fontconfig libdbus-1-3
                      libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxcb-shape0 libXcomposite1
                      libXdamage1 libXrandr2 libXtst6 libxkbfile1)
            install_command=(sudo zypper install -y)
        fi
        if [ ${#packages[@]} -gt 0 ] && confirm "Install them now (may ask for your password)?" y; then
            "${install_command[@]}" "${packages[@]}" || warn "Some packages could not be installed; S2 Watch may not start."
        else
            warn "Install the libraries above with your package manager if S2 Watch does not start."
        fi
    fi
else
    info "Not needed on macOS."
fi

# ---- 4. Program and Python packages -------------------------------------------------------
bold "4/7  Installing S2 Watch $VERSION and its Python packages"
mkdir -p "$PREFIX"
if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    info "Replacing an environment built with an older Python..."
    rm -rf "$VENV"
fi
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
info "Downloading and installing packages (PySide6 is large; this can take several minutes)..."
"$VENV/bin/python" -m pip install --upgrade --quiet pip
if [ -e "$SOURCE_DIR/.git" ]; then
    # From a git clone: S2 Watch runs the clone's code, so "git pull" updates it.
    "$VENV/bin/python" -m pip install --upgrade --progress-bar on -e "$SOURCE_DIR"
    "$VENV/bin/python" -c 'from s2_watch.checkout import record_requirements; record_requirements()'
    info "OK: installed from the git clone in $SOURCE_DIR. Update later with: git pull"
    info "Keep that folder; S2 Watch runs from it."
else
    "$VENV/bin/python" -m pip install --upgrade --progress-bar on "$SOURCE_DIR"
    info "OK: installed."
fi

# ---- 5. Check the installation ------------------------------------------------------------
bold "5/7  Checking the installation"
"$VENV/bin/python" - <<'PY' || fail "S2 Watch's packages did not load. See the messages above."
import importlib
for module in ("s2_watch", "s2_watch.ui.main_window", "PySide6.QtWebEngineWidgets", "PySide6.QtSvg"):
    importlib.import_module(module)
import s2_watch
print(f"  OK: S2 Watch {s2_watch.__version__} and the map engine load.")
PY

# ---- 6. Data, launchers, shortcuts --------------------------------------------------------
bold "6/7  Data and shortcuts"
import_database() {  # a consistent copy, even of a database another S2 Watch has open
    "$VENV/bin/python" -c 'import sqlite3, sys; source = sqlite3.connect(sys.argv[1]); target = sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()' "$1" "$DATA"
}
if [ ! -f "$DATA" ]; then
    # Look for an earlier S2 Watch's database (a build folder, a copy...), newest first.
    found=()
    info "Looking for existing S2 Watch data in your home folder..."
    while IFS= read -r candidate; do found+=("$candidate"); done < <("$VENV/bin/python" - "$HOME" "$PREFIX" <<'FIND'
import os, sys
home, prefix = sys.argv[1], os.path.realpath(sys.argv[2])
skip = {"backups", ".cache", "Trash", "node_modules", ".git", "venv", ".venv", "Library"}
found = []
for root, folders, files in os.walk(home):
    if root[len(home):].count(os.sep) >= 6 or os.path.realpath(root).startswith(prefix):
        folders[:] = []
        continue
    folders[:] = [folder for folder in folders if folder not in skip]
    if "s2_watch.db" in files:
        path = os.path.join(root, "s2_watch.db")
        found.append((os.path.getmtime(path), path))
for _mtime, path in sorted(found, reverse=True)[:5]:
    print(path)
FIND
)
    existing=""
    if [ ${#found[@]} -gt 0 ]; then
        info "Found existing S2 Watch data (settings, watch forms, labels, history):"
        for index in "${!found[@]}"; do
            info "  $((index + 1)). ${found[$index]}  (last changed $(date -r "${found[$index]}" '+%Y-%m-%d %H:%M' 2>/dev/null))"
        done
        if [ "$ASSUME_YES" = 1 ]; then
            existing="${found[0]}"
        else
            read -r -p "  Import which one? [1] (0 = start fresh, or type another path) " choice || choice=""
            choice="${choice:-1}"
            if [[ "$choice" =~ ^[0-9]+$ ]]; then
                [ "$choice" -ge 1 ] && [ "$choice" -le ${#found[@]} ] && existing="${found[$((choice - 1))]}"
            else
                existing="${choice/#\~/$HOME}"
            fi
        fi
    elif [ "$ASSUME_YES" = 0 ]; then
        read -r -p "  Import an existing S2 Watch database (s2_watch.db)? Enter its path, or press Enter to skip: " existing || existing=""
        existing="${existing/#\~/$HOME}"
    fi
    if [ -n "$existing" ]; then
        if [ -f "$existing" ] && import_database "$existing"; then info "Imported $existing"
        else warn "$existing could not be imported; starting with a new database."; fi
    fi
fi
ICON_DIR="$("$VENV/bin/python" -c 'import s2_watch, pathlib; print(pathlib.Path(s2_watch.__file__).parent / "data" / "icons")')"

if [ "$OS" = "Darwin" ]; then
    BUNDLE="$HOME/Applications/S2 Watch.app"
    mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
    cat > "$BUNDLE/Contents/MacOS/s2-watch" <<EOF
#!/bin/bash
exec "$VENV/bin/python" -m s2_watch.main --database "$DATA" "\$@"
EOF
    chmod +x "$BUNDLE/Contents/MacOS/s2-watch"
    cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>S2 Watch</string>
  <key>CFBundleDisplayName</key><string>S2 Watch</string>
  <key>CFBundleIdentifier</key><string>com.github.n1rog.s2watch</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>s2-watch</string>
  <key>CFBundleIconFile</key><string>s2-watch</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF
    # Icon: render the SVG to PNGs with Qt, then build an .icns with macOS's iconutil.
    ICONSET="$(mktemp -d)/s2-watch.iconset"
    mkdir -p "$ICONSET"
    "$VENV/bin/python" - "$ICON_DIR" "$ICONSET" <<'PY' || true
import sys
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QRectF, Qt
app = QGuiApplication(["icons", "-platform", "offscreen"])
icons, out = sys.argv[1], sys.argv[2]
for size in (16, 32, 64, 128, 256, 512, 1024):
    renderer = QSvgRenderer(f"{icons}/{'s2-watch-small.svg' if size <= 32 else 's2-watch.svg'}")
    image = QImage(size, size, QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image); renderer.render(painter, QRectF(0, 0, size, size)); painter.end()
    names = {16: ["icon_16x16"], 32: ["icon_16x16@2x", "icon_32x32"], 64: ["icon_32x32@2x"], 128: ["icon_128x128"],
             256: ["icon_128x128@2x", "icon_256x256"], 512: ["icon_256x256@2x", "icon_512x512"], 1024: ["icon_512x512@2x"]}
    for name in names[size]:
        image.save(f"{out}/{name}.png")
PY
    iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/s2-watch.icns" 2>/dev/null || warn "Could not make the app icon."
    mkdir -p "$PREFIX/bin"
    cat > "$PREFIX/bin/s2-watch" <<EOF
#!/bin/bash
exec "$VENV/bin/python" -m s2_watch.main --database "$DATA" "\$@"
EOF
    chmod +x "$PREFIX/bin/s2-watch"
    LAUNCHER="$PREFIX/bin/s2-watch"
    info "App: $BUNDLE (in your Applications folder; drag it to the Dock if you like)"
else
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
    cat > "$HOME/.local/bin/s2-watch" <<EOF
#!/bin/bash
# S2 Watch launcher (created by the installer). Use --configure to rerun the setup wizard.
exec "$VENV/bin/python" -m s2_watch.main --database "$DATA" "\$@"
EOF
    chmod +x "$HOME/.local/bin/s2-watch"
    LAUNCHER="$HOME/.local/bin/s2-watch"
    cp "$ICON_DIR/s2-watch.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/s2-watch.svg"
    DESKTOP_FILE="$HOME/.local/share/applications/s2-watch.desktop"
    if [ -f "$DESKTOP_FILE" ] && ! grep -q "$LAUNCHER" "$DESKTOP_FILE"; then
        cp "$DESKTOP_FILE" "$DESKTOP_FILE.before-installer"
        info "Your previous menu entry was saved as $DESKTOP_FILE.before-installer"
    fi
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=S2 Watch
Comment=Monitor radio messaging applications for newsworthy reports
Exec=$LAUNCHER
Icon=s2-watch
Terminal=false
Categories=Network;HamRadio;
StartupWMClass=s2-watch
EOF
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    info "Menu entry: S2 Watch (Internet / Ham Radio)"
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    if [ -d "$DESKTOP_DIR" ] && confirm "Add an S2 Watch shortcut to your desktop?" n; then
        cp "$DESKTOP_FILE" "$DESKTOP_DIR/s2-watch.desktop"
        chmod +x "$DESKTOP_DIR/s2-watch.desktop"
        gio set "$DESKTOP_DIR/s2-watch.desktop" metadata::trusted true 2>/dev/null || true
    fi
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "$HOME/.local/bin is not on your PATH; the menu entry works, but to type 's2-watch' add it to PATH." ;;
    esac
fi

cp "$SCRIPT_DIR/uninstall.sh" "$PREFIX/uninstall.sh" 2>/dev/null && chmod +x "$PREFIX/uninstall.sh" || true

# ---- 7. Configuration ---------------------------------------------------------------------
bold "7/7  Configuration"
if [ "$RUN_WIZARD" = 1 ]; then
    "$LAUNCHER" --configure || warn "Setup was not completed; run '$LAUNCHER --configure' any time."
else
    info "Skipped. Run '$LAUNCHER --configure' to walk through the settings."
fi

bold "S2 Watch $VERSION is installed."
info "Start it from your applications menu, or run: $LAUNCHER"
info "Change settings later: $LAUNCHER --configure (or the app's Configuration menu)"
info "Uninstall: $PREFIX/uninstall.sh"
if [ "$ASSUME_YES" = 0 ] && confirm "Start S2 Watch now?" y; then
    nohup "$LAUNCHER" >/dev/null 2>&1 &
fi
