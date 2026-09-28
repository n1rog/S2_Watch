S2 Watch - installation
=======================

The installer checks for everything S2 Watch needs, downloads anything that is missing,
installs S2 Watch, creates shortcuts, and then walks you through the settings:
which radio applications to watch, how far back to look, your MARS/FEMA focus regions,
watch forms, analysis mode, alerts, map layers, time zone and theme.

You need an internet connection (about 400 MB is downloaded the first time).
Nothing is installed system-wide without asking you first.

Windows 10/11
  1. Unzip this folder.
  2. Double-click install.cmd.
  Installs to %LOCALAPPDATA%\S2Watch. Adds Start menu items: S2 Watch, S2 Watch Setup,
  Uninstall S2 Watch.

macOS 11 or newer
  1. Unzip this folder.
  2. Open Terminal, type "bash " (with a space), drag install.sh into the window, press Return.
  Installs to ~/Library/Application Support/S2 Watch and adds "S2 Watch" to
  ~/Applications.

Linux (Ubuntu/Debian, Fedora, Arch, openSUSE)
  1. Unzip this folder.
  2. In a terminal:  bash install.sh
  Installs to ~/.local/share/s2-watch, adds the "s2-watch" command in ~/.local/bin and a
  menu entry.

What gets checked and installed
  - Python 3.11 or newer (installed if missing: winget/python.org on Windows,
    Homebrew/python.org on macOS, your package manager on Linux)
  - On Linux, the system libraries Qt's window and map need
  - On Windows, the Microsoft Visual C++ runtime
  - S2 Watch's Python packages (PySide6), in S2 Watch's own environment

Already have S2 Watch data?
  The installer offers to import an existing s2_watch.db, so your settings, watch forms
  and history come with you.

Changing settings later
  Use the app's Configuration menu, or rerun the setup walk-through:
    Windows: Start menu > S2 Watch > S2 Watch Setup
    macOS:   ~/Library/Application\ Support/S2\ Watch/bin/s2-watch --configure
    Linux:   s2-watch --configure

Upgrading
  Run the new version's installer. Your data and settings are kept.

Uninstalling
    Windows: Start menu > S2 Watch > Uninstall S2 Watch
    macOS/Linux: bash <install folder>/uninstall.sh
  Your data is kept unless you choose to delete it.
