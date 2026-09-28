#!/usr/bin/env bash
# S2 Watch uninstaller for Linux and macOS. Removes the program, launchers and shortcuts;
# your settings and history are kept unless you choose to delete them.
set -euo pipefail

OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
    PREFIX="$HOME/Library/Application Support/S2 Watch"
else
    PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/s2-watch"
fi

ask() { local answer; read -r -p "$1 [$2] " answer || answer=""; answer="${answer:-$2}"; [[ "$answer" =~ ^[Yy] ]]; }

echo "This removes S2 Watch from $PREFIX and its launchers."
ask "Continue?" "y/N" || { echo "Nothing was removed."; exit 0; }

rm -rf "$PREFIX/venv" "$PREFIX/bin"
if [ "$OS" = "Darwin" ]; then
    rm -rf "$HOME/Applications/S2 Watch.app"
else
    rm -f "$HOME/.local/bin/s2-watch" "$HOME/.local/share/icons/hicolor/scalable/apps/s2-watch.svg"
    DESKTOP_FILE="$HOME/.local/share/applications/s2-watch.desktop"
    rm -f "$DESKTOP_FILE"
    if [ -f "$DESKTOP_FILE.before-installer" ]; then
        mv "$DESKTOP_FILE.before-installer" "$DESKTOP_FILE"
        echo "Restored your earlier S2 Watch menu entry."
    fi
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    rm -f "$DESKTOP_DIR/s2-watch.desktop"
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

if [ -f "$PREFIX/s2_watch.db" ] && ask "Also delete your S2 Watch settings and history ($PREFIX)?" "y/N"; then
    rm -rf "$PREFIX"
    echo "Settings and history deleted."
else
    echo "Your settings and history were kept in $PREFIX (a reinstall will use them)."
fi
echo "S2 Watch was removed."
