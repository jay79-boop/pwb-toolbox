<#
.SYNOPSIS
  Put a "Karaoke" icon on the Desktop that starts karaoke night in one
  double-click. Run this once.

.DESCRIPTION
  The icon points at powershell.exe with '-ExecutionPolicy Bypass -File', not
  at the .ps1 directly. Two reasons, and both have already bitten:

    * Windows does not RUN a .ps1 on double-click, it opens it in Notepad.
    * This machine's execution policy blocks unsigned scripts, so a shortcut
      that merely launches PowerShell would die with a red wall of text about
      "running scripts is disabled on this system" -- which reads exactly
      like karaoke being broken.

  Idempotent: run it as many times as you like. WScript.Shell.CreateShortcut
  opens an existing .lnk for update, so the second run rewrites the same file
  rather than leaving 'Karaoke - Copy' next to it.

  The shortcut is read back off disk afterwards and compared with what was
  asked for. This script's own "created it" message is not evidence.

  Windows PowerShell 5.1: ASCII bytes only, no bash-style command
  chaining, no '~' for home, no
  here-strings.

.PARAMETER Name
  Shortcut name, without .lnk. Default 'Karaoke'.

.PARAMETER Port
  Bake a non-default port into the shortcut.

.PARAMETER Remove
  Delete the shortcut instead of creating it.

.EXAMPLE
  .\tools\karaoke_server\install_shortcut.ps1
#>
[CmdletBinding()]
param(
  [string] $Name = 'Karaoke',

  [int] $Port = 0,

  [switch] $Remove
)

$ErrorActionPreference = 'Stop'

$launcher = Join-Path $PSScriptRoot 'start_karaoke.ps1'
if (-not (Test-Path -LiteralPath $launcher)) {
  throw "start_karaoke.ps1 is not next to this script. Expected: $launcher"
}

# Derived from where this file sits, never a hard-coded user path: there are
# two pwb-toolbox checkouts on this machine, and the icon must point at the
# one this script was run from.
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) {
  throw 'Windows did not report a Desktop folder for this account.'
}
$linkPath = Join-Path $desktop ($Name + '.lnk')

if ($Remove) {
  if (Test-Path -LiteralPath $linkPath) {
    Remove-Item -LiteralPath $linkPath -Force
    Write-Host "Removed $linkPath"
  } else {
    Write-Host "Nothing to remove: $linkPath does not exist."
  }
  exit 0
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powershell)) {
  $found = Get-Command powershell.exe -ErrorAction SilentlyContinue
  if (-not $found) { throw 'Could not find powershell.exe on this computer.' }
  $powershell = $found.Source
}

# -NoProfile so a slow or broken profile cannot delay or break karaoke night.
# -ExecutionPolicy Bypass because this machine blocks unsigned .ps1 files.
# -File (not -Command) so the path is taken as a path and nothing in it is
# executed as script.
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"'
if ($Port -gt 0) {
  $arguments = $arguments + ' -Port ' + $Port
}

$shell = New-Object -ComObject WScript.Shell
try {
  $shortcut = $shell.CreateShortcut($linkPath)
  $shortcut.TargetPath = $powershell
  $shortcut.Arguments = $arguments
  $shortcut.WorkingDirectory = $repoRoot
  $shortcut.Description = 'Start karaoke night. The big screen opens by itself.'
  $shortcut.WindowStyle = 1
  $shortcut.Save()

  # Read it back. A .lnk that saved without error can still have been written
  # somewhere redirected (OneDrive Desktop backup moves the folder), so the
  # check is on the file, not on the fact that Save() returned.
  $check = $shell.CreateShortcut($linkPath)
  # Copied out as plain strings before the COM object goes away: nothing below
  # this block should still be talking to WScript.Shell.
  $checkTarget = "$($check.TargetPath)"
  $checkArgs = "$($check.Arguments)"
} finally {
  try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) } catch { }
}

if (-not (Test-Path -LiteralPath $linkPath)) {
  throw "The shortcut was not written: $linkPath"
}
if ($checkTarget -ne $powershell -or $checkArgs -ne $arguments) {
  throw "The shortcut on disk does not match what was asked for: $linkPath"
}

Write-Host ''
Write-Host 'Done. There is now a Karaoke icon on your Desktop:' -ForegroundColor Green
Write-Host "    $linkPath"
Write-Host ''
Write-Host 'Double-click it to start karaoke. The big screen opens by itself.'
Write-Host 'To see the icon now, run:'
Write-Host "    explorer.exe /select,`"$linkPath`""
exit 0
