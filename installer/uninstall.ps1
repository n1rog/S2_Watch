<#
S2 Watch uninstaller for Windows. Removes the program and shortcuts; your settings and
history are kept unless you choose to delete them.
#>
$ErrorActionPreference = 'Stop'
$Prefix = Join-Path $env:LOCALAPPDATA 'S2Watch'

$answer = Read-Host "This removes S2 Watch from $Prefix and its shortcuts. Continue? [y/N]"
if (-not $answer.Trim().ToLower().StartsWith('y')) { Write-Host 'Nothing was removed.'; exit 0 }

Remove-Item -Recurse -Force (Join-Path $Prefix 'venv') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $Prefix 's2-watch.ico') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path ([Environment]::GetFolderPath('Programs')) 'S2 Watch') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path ([Environment]::GetFolderPath('Desktop')) 'S2 Watch.lnk') -ErrorAction SilentlyContinue

$data = Join-Path $Prefix 'data'
if (Test-Path $data) {
    $answer = Read-Host "Also delete your S2 Watch settings and history ($data)? [y/N]"
    if ($answer.Trim().ToLower().StartsWith('y')) { Remove-Item -Recurse -Force $Prefix; Write-Host 'Settings and history deleted.' }
    else { Write-Host "Your settings and history were kept in $data (a reinstall will use them)." }
}
Write-Host 'S2 Watch was removed.'
Read-Host 'Press Enter to close'
