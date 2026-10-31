# Installing S2 Watch

S2 Watch runs on Windows 10/11, macOS 11 or newer, and Linux (Ubuntu/Debian, Fedora,
Arch, openSUSE). The installer checks for everything it needs, downloads what is
missing, installs S2 Watch, adds shortcuts, and then walks you through the settings.

You need an internet connection; about 400 MB is downloaded the first time. Nothing is
installed system-wide without asking you first.

## 1. Get S2 Watch

There are two ways. Both use the same installer and keep your data in the same place.

**With Git (recommended): update any time with `git pull`.** You need
[Git](https://git-scm.com/downloads); on Windows you can install it with
`winget install Git.Git`. In a terminal (Command Prompt or PowerShell on Windows), in the
folder where you want S2 Watch's files to live:

```bash
git clone https://github.com/n1rog/S2_Watch.git
```

This makes an `S2_Watch` folder. Keep it: S2 Watch runs from it. The installer is in its
`installer` folder.

**Or download a release package.** Open the
[latest release](https://github.com/n1rog/S2_Watch/releases/latest) and, under
**Assets**, download `S2Watch-<version>-installer.zip`. The same package works on all
three systems. Unzip it. To update, you download and run each new release.

## 2. Install

### Windows

1. Open the unzipped folder (with Git: `S2_Watch\installer`).
2. Double-click **`install.cmd`**.
3. Answer the questions in the window that opens.

S2 Watch is installed in `%LOCALAPPDATA%\S2Watch`. The Start menu gets **S2 Watch**,
**S2 Watch Setup** and **Uninstall S2 Watch**.

If Windows says it protected your PC, choose **More info → Run anyway**. The installer
is a script, not a signed program.

### macOS

1. Open **Terminal**.
2. Type `bash ` (with a space after it), drag **`install.sh`** from the unzipped folder
   (with Git: `S2_Watch/installer`) into the Terminal window, and press Return.
3. Answer the questions.

S2 Watch is installed in `~/Library/Application Support/S2 Watch`, and **S2 Watch** is
added to `~/Applications`.

### Linux

In a terminal:

```bash
bash install.sh                    # in the unzipped folder
bash S2_Watch/installer/install.sh # or, with Git, where you cloned it
```

S2 Watch is installed in `~/.local/share/s2-watch`, with the `s2-watch` command in
`~/.local/bin` and an entry in your applications menu. The installer may ask for your
password to install the system libraries Qt needs.

## What the installer checks and installs

- Python 3.11 or newer, if missing (winget or python.org on Windows, Homebrew or
  python.org on macOS, your package manager on Linux)
- On Linux, the system libraries the window and map need
- On Windows, the Microsoft Visual C++ runtime
- S2 Watch's Python packages (PySide6), in S2 Watch's own environment, so nothing
  else on your computer is affected

## 3. Set up

After installing, the setup walk-through asks:

- **Sources:** which radio applications to watch: JS8Call, MagCommstat, Commstat,
  FLMSG, JS8SuperSpotter, and OSINT (official and news feeds). It looks for each one's
  files and suggests what it finds.
- **Watch period:** how far back to look.
- **Focus regions:** your MARS/FEMA regions.
- **Watch forms**, **analysis**, **alerts**, **map layers**, **time format** and
  **theme**.

For JS8Call, turn on its TCP API (in JS8Call: **File → Settings → Reporting**) before
you start S2 Watch. The default port is 2442.

You can change everything later from the app's **Configuration** menu, or rerun the
walk-through:

| System  | Rerun setup |
|---------|-------------|
| Windows | Start menu → **S2 Watch Setup** |
| macOS   | `~/Library/Application\ Support/S2\ Watch/bin/s2-watch --configure` |
| Linux   | `s2-watch --configure` |

## Moving from another computer or an older copy

The installer looks for an existing `s2_watch.db` and offers to import it. Your settings,
watch forms, reports, labels and history then come with you. You can also type the path
to a database you copied over.

## Updating

**From S2 Watch (Linux and macOS, installed with the installer package):** when a new
version is out, the button beside the clock turns orange (**Update to 0.42.0**). Click it:
S2 Watch downloads the update, closes, saves a copy of its database, installs the update and
opens again.

Otherwise, quit S2 Watch first. Your data and settings are kept either way.

**Installed with Git:** in a terminal, in the `S2_Watch` folder:

```bash
git pull
```

Then start S2 Watch. If an update needs new or newer Python packages, S2 Watch installs
them as it starts. On Windows a window shows the progress; that start can take a few
minutes when the update includes a new Qt.

**Installed from a release package:** download the new release and run its installer the
same way.

The version shows in **Help → About S2 Watch**.

### Switching an existing install to Git

Clone S2 Watch as in step 1 and run the installer from the clone's `installer` folder. It
replaces the program in the same place and keeps your data, settings and shortcuts.
From then on, `git pull` updates it.

If you ran an older S2 Watch straight from an unzipped folder (no installer), the
installer offers to import that folder's `s2_watch.db`. After checking that your data came
across, delete the old folder so you don't start the old copy by mistake.

## Your data and backups

Everything lives in one file, `s2_watch.db`; the installer shows its location. Once a day,
S2 Watch copies it into a `backups` folder beside it and keeps the last 14 copies.
**Configuration → Back Up Now** makes an extra copy, and **Open Backups Folder** finds
them. To restore one, quit S2 Watch and copy a backup over `s2_watch.db`.

## Optional: Ollama

S2 Watch works without AI. With [Ollama](https://ollama.com) on the same computer, it
can double-check what the rules flag, check unmatched radio traffic in the background
(**Possible Misses**), and suggest assessments in reports and briefings. Install Ollama,
run `ollama pull llama3.2`, then choose **Configuration → Analysis Mode → Local Ollama
with rules fallback**. A graphics card is not required; on a normal CPU it takes several
seconds per message.

## Troubleshooting

- **Map markers, aircraft or regions vanish while zooming:** some graphics drivers
  (seen on Intel Iris Xe) do this. Turn on **Configuration → Display… → Software map
  rendering**, then restart S2 Watch.
- **No JS8Call messages:** check that JS8Call is running with its TCP API on, and that
  the host and port under **Configuration → Sources…** match it.
- **The installer stopped partway:** fix what it reports, then run it again. Running
  it again is safe and keeps your data.
- **`git pull` says your local changes would be overwritten:** you (or an editor) changed
  a file in the clone. `git stash` sets your changes aside; then `git pull` again.
- **S2 Watch won't start after `git pull`:** run the installer from the clone's
  `installer` folder again. It repairs the installation and keeps your data.

## Uninstalling

| System        | Uninstall |
|---------------|-----------|
| Windows       | Start menu → **Uninstall S2 Watch** |
| macOS / Linux | `bash <install folder>/uninstall.sh` |

Your data is kept unless you choose to delete it. With Git, also delete the `S2_Watch`
folder.

## Installer options

For scripted or repeat installs:

| Linux / macOS  | Windows      | Effect |
|----------------|--------------|--------|
| `--yes`        | `-Yes`       | Accept the default answer to the installer's own questions |
| `--no-wizard`  | `-NoWizard`  | Skip the setup walk-through (run it later with `--configure`) |
