<#
S2 Watch installer for Windows.

Checks for and installs everything S2 Watch needs (Python 3.11+, the Microsoft Visual C++
runtime, and the Python packages), installs S2 Watch into its own Python environment,
creates Start menu (and optionally desktop) shortcuts, then walks you through configuration.
Safe to run again: it upgrades the program and keeps your data and settings.

Run it by double-clicking install.cmd, or:  powershell -ExecutionPolicy Bypass -File install.ps1
Options: -Yes (accept the installer's default answers)  -NoWizard (skip configuration)
#>
param([switch]$Yes, [switch]$NoWizard)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # much faster downloads with Invoke-WebRequest

$ScriptDir = $PSScriptRoot
if (Test-Path (Join-Path $ScriptDir 'app\pyproject.toml')) { $SourceDir = Join-Path $ScriptDir 'app' }
elseif (Test-Path (Join-Path $ScriptDir '..\pyproject.toml')) { $SourceDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path }
else { Write-Host 'Cannot find the S2 Watch program files next to this installer.' -ForegroundColor Red; exit 1 }
$Version = (Select-String -Path (Join-Path $SourceDir 's2_watch\__init__.py') -Pattern '^__version__ = "(.*)"').Matches[0].Groups[1].Value

$Prefix = Join-Path $env:LOCALAPPDATA 'S2Watch'
$Venv = Join-Path $Prefix 'venv'
$DataDir = Join-Path $Prefix 'data'
$Data = Join-Path $DataDir 's2_watch.db'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvPythonW = Join-Path $Venv 'Scripts\pythonw.exe'
$PythonVersion = '3.12.7'

function Heading($text) { Write-Host ''; Write-Host $text -ForegroundColor Cyan }
function Info($text) { Write-Host "  $text" }
function Warn($text) { Write-Host "  ! $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host ''; Write-Host "Installation stopped: $text" -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }
function Confirm($question, [bool]$default = $true) {
    if ($Yes) { return $default }
    $hint = if ($default) { 'Y/n' } else { 'y/N' }
    $answer = Read-Host "  $question [$hint]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith('y')
}

Heading "S2 Watch $Version installer (Windows)"
Info "Program:  $Venv"
Info "Data:     $Data"

# ---- 1. Internet ---------------------------------------------------------------------------
Heading '1/7  Checking the internet connection'
try {
    Invoke-WebRequest -Uri 'https://pypi.org/simple/pyside6/' -UseBasicParsing -TimeoutSec 20 | Out-Null
    Info 'OK: can reach the Python package index.'
} catch { Fail 'cannot reach https://pypi.org. The installer downloads Python packages (~400 MB); connect and try again.' }

# ---- 2. Python 3.11+ -----------------------------------------------------------------------
Heading '2/7  Checking for Python 3.11 or newer'
function Find-Python {
    $check = 'import sys, venv; assert sys.version_info >= (3, 11); print(sys.executable)'
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($minor in '3.13', '3.12', '3.11') { $candidates += ,@('py', "-$minor") }
    }
    foreach ($path in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe", "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe")) {
        if (Test-Path $path) { $candidates += ,@($path) }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += ,@('python') }
    foreach ($candidate in $candidates) {
        try {
            $exe = $candidate[0]; $arguments = @($candidate | Select-Object -Skip 1) + @('-c', $check)
            $result = & $exe @arguments 2>$null
            if ($LASTEXITCODE -eq 0 -and $result -and (Test-Path $result.Trim())) { return $result.Trim() }
        } catch { }
    }
    return $null
}
$Python = Find-Python
if ($Python) {
    Info "OK: $(& $Python --version) ($Python)"
} else {
    Warn 'Python 3.11 or newer was not found.'
    if (-not (Confirm 'Install Python now (for your user account only)?')) { Fail 'Python 3.11+ is required.' }
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info 'Installing Python 3.12 with winget...'
        & winget install --exact --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        $installed = ($LASTEXITCODE -eq 0)
    }
    if (-not $installed) {
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
        $file = "python-$PythonVersion-$arch.exe"
        $installer = Join-Path $env:TEMP $file
        Info "Downloading $file from python.org..."
        Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/$file" -OutFile $installer -UseBasicParsing
        Info 'Installing it...'
        Start-Process -FilePath $installer -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0' -Wait
        Remove-Item $installer -ErrorAction SilentlyContinue
    }
    $Python = Find-Python
    if (-not $Python) { Fail 'Python was installed but could not be found. Open a new window and run the installer again.' }
    Info "OK: $(& $Python --version) ($Python)"
}

# ---- 3. Visual C++ runtime -----------------------------------------------------------------
Heading "3/7  Checking the Microsoft Visual C++ runtime (needed by S2 Watch's window and map)"
$runtime = Join-Path $env:SystemRoot 'System32\msvcp140.dll'
if (Test-Path $runtime) {
    Info 'OK: present.'
} else {
    Warn 'The Microsoft Visual C++ runtime is missing.'
    if (Confirm 'Install it now (Windows will ask for permission)?') {
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
        $vcInstaller = Join-Path $env:TEMP "vc_redist.$arch.exe"
        Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vc_redist.$arch.exe" -OutFile $vcInstaller -UseBasicParsing
        Start-Process -FilePath $vcInstaller -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait
        Remove-Item $vcInstaller -ErrorAction SilentlyContinue
    } else { Warn 'S2 Watch may not start without it.' }
}

# ---- 4. Program and Python packages --------------------------------------------------------
Heading "4/7  Installing S2 Watch $Version and its Python packages"
New-Item -ItemType Directory -Force -Path $Prefix, $DataDir | Out-Null
if ((Test-Path $VenvPython) -and -not ((& $VenvPython -c 'import sys; print(sys.version_info >= (3, 11))') -eq 'True')) {
    Info 'Replacing an environment built with an older Python...'
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $VenvPython)) { & $Python -m venv $Venv; if ($LASTEXITCODE -ne 0) { Fail 'could not create the Python environment.' } }
Info 'Downloading and installing packages (PySide6 is large; this can take several minutes)...'
& $VenvPython -m pip install --upgrade --quiet pip
& $VenvPython -m pip install --upgrade $SourceDir
if ($LASTEXITCODE -ne 0) { Fail 'installing the Python packages failed; see the messages above.' }
Info 'OK: installed.'

# ---- 5. Check the installation -------------------------------------------------------------
Heading '5/7  Checking the installation'
$checkScript = @'
import importlib
for module in ("s2_watch", "s2_watch.ui.main_window", "PySide6.QtWebEngineWidgets", "PySide6.QtSvg"):
    importlib.import_module(module)
import s2_watch
print(f"  OK: S2 Watch {s2_watch.__version__} and the map engine load.")
'@
& $VenvPython -c $checkScript
if ($LASTEXITCODE -ne 0) { Fail "S2 Watch's packages did not load. See the messages above." }

# ---- 6. Data and shortcuts -----------------------------------------------------------------
Heading '6/7  Data and shortcuts'
if (-not (Test-Path $Data) -and -not $Yes) {
    $existing = Read-Host '  Import an existing S2 Watch database (s2_watch.db)? Enter its path, or press Enter to skip'
    if ($existing) {
        $existing = $existing.Trim('"')
        if (Test-Path $existing) { Copy-Item $existing $Data; Info "Imported $existing" }
        else { Warn "$existing was not found; starting with a new database." }
    }
}
$iconFile = Join-Path $Prefix 's2-watch.ico'
$iconScript = @'
import sys, pathlib, s2_watch
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QRectF, Qt
app = QGuiApplication(["icon", "-platform", "offscreen"])
icons = pathlib.Path(s2_watch.__file__).parent / "data" / "icons"
image = QImage(256, 256, QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image); QSvgRenderer(str(icons / "s2-watch.svg")).render(painter, QRectF(0, 0, 256, 256)); painter.end()
image.save(sys.argv[1])
'@
& $VenvPython -c $iconScript $iconFile
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut($path, $target, $arguments, $description) {
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $Prefix
    $shortcut.Description = $description
    if (Test-Path $iconFile) { $shortcut.IconLocation = $iconFile }
    $shortcut.Save()
}
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'S2 Watch'
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
$runArguments = "-m s2_watch.main --database `"$Data`""
New-Shortcut (Join-Path $startMenu 'S2 Watch.lnk') $VenvPythonW $runArguments 'Monitor radio messaging applications for newsworthy reports'
New-Shortcut (Join-Path $startMenu 'S2 Watch Setup.lnk') $VenvPython "$runArguments --configure" 'Change S2 Watch settings'
Copy-Item (Join-Path $ScriptDir 'uninstall.ps1') (Join-Path $Prefix 'uninstall.ps1') -Force -ErrorAction SilentlyContinue
New-Shortcut (Join-Path $startMenu 'Uninstall S2 Watch.lnk') 'powershell.exe' "-NoProfile -ExecutionPolicy Bypass -File `"$Prefix\uninstall.ps1`"" 'Remove S2 Watch'
Info 'Start menu: S2 Watch, S2 Watch Setup, Uninstall S2 Watch'
if (Confirm 'Add an S2 Watch shortcut to your desktop?' $false) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'S2 Watch.lnk') $VenvPythonW $runArguments 'S2 Watch'
}

# ---- 7. Configuration ----------------------------------------------------------------------
Heading '7/7  Configuration'
if (-not $NoWizard) {
    & $VenvPython -m s2_watch.main --database $Data --configure
    if ($LASTEXITCODE -ne 0) { Warn "Setup was not completed; run 'S2 Watch Setup' from the Start menu any time." }
} else { Info "Skipped. Run 'S2 Watch Setup' from the Start menu to walk through the settings." }

Heading "S2 Watch $Version is installed."
Info "Start it from the Start menu (S2 Watch). Change settings with 'S2 Watch Setup' or the app's Configuration menu."
if (-not $Yes -and (Confirm 'Start S2 Watch now?')) {
    Start-Process -FilePath $VenvPythonW -ArgumentList '-m', 's2_watch.main', '--database', "`"$Data`"" -WorkingDirectory $Prefix
}
