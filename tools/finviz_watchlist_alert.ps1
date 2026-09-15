<#
.SYNOPSIS
  Daily watchlist check for the Windows Scheduled Task
  (tools\install_finviz_watchlist_task.ps1) to run. Pops a message only
  when something is actually flagged -- a quiet day means nothing showed
  up, not that the check didn't run (see the log file below for that).

.DESCRIPTION
  Runs `python tools\finviz_scan.py check`, and if its exit code says
  something was flagged (2), shows a TopMost, owned MessageBox with a
  system beep. A bare MessageBox::Show from a scheduled task renders
  behind whatever already has focus and gets missed entirely -- the same
  trap tools\desk_agent's alert jobs had to avoid -- so this deliberately
  gives it a small TopMost owner form instead.

  Every run's output is also written to
  tools\finviz_scan_watchlist_task.log (overwritten each run), so a quiet
  day is checkable after the fact rather than just trusted.

  Not meant to be run by hand day to day -- use the menu's "Check your
  watchlist" option for that. This is the unattended half, registered by
  tools\install_finviz_watchlist_task.ps1.

.PARAMETER WatchlistFile
  Watchlist to check instead of the default finviz\watchlist.txt.

.PARAMETER OutDir
  Folder for the saved report instead of Desktop\finviz-research.

.PARAMETER LogPath
  Run log instead of tools\finviz_scan_watchlist_task.log.

.PARAMETER NoPopup
  Write the message the popup would show into the log instead of showing
  it. For testing: a real MessageBox waits for a click.
#>
[CmdletBinding()]
param(
  [string] $WatchlistFile,
  [string] $OutDir,
  [string] $LogPath,
  [switch] $NoPopup
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if ($LogPath) { $logPath = $LogPath } else { $logPath = Join-Path $repoRoot 'tools\finviz_scan_watchlist_task.log' }

function Show-FinvizPopup {
  param(
    [string] $Text,
    [string] $Title,
    [string] $IconName
  )
  if ($NoPopup) {
    $line = "`r`nPOPUP [" + $Title + "]: " + $Text + "`r`n"
    [IO.File]::AppendAllText($logPath, $line, (New-Object Text.UTF8Encoding $false))
    return
  }
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $owner = New-Object System.Windows.Forms.Form
  $owner.TopMost = $true
  $owner.StartPosition = 'CenterScreen'
  $owner.Size = New-Object System.Drawing.Size(1, 1)
  $owner.ShowInTaskbar = $false
  $owner.Show()
  $owner.Focus() | Out-Null
  [System.Media.SystemSounds]::Exclamation.Play()
  $icon = [System.Windows.Forms.MessageBoxIcon]::$IconName
  [System.Windows.Forms.MessageBox]::Show($owner, $Text, $Title,
    [System.Windows.Forms.MessageBoxButtons]::OK, $icon) | Out-Null
  $owner.Close()
}

try {
  $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
  } else {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { throw 'No Python found (checked .venv\Scripts\python.exe and PATH).' }
    $python = $found.Source
  }

  $env:FINVIZ_DESKTOP = [Environment]::GetFolderPath('Desktop')

  $checkArgs = @((Join-Path $repoRoot 'tools\finviz_scan.py'), 'check', '--quiet')
  if ($WatchlistFile) { $checkArgs += @('--file', $WatchlistFile) }
  if ($OutDir) { $checkArgs += @('--out-dir', $OutDir) }

  Push-Location $repoRoot
  # Windows PowerShell 5.1 turns every stderr line of a native program into a
  # terminating error under ErrorActionPreference Stop when 2>&1 is used.
  # yfinance writes warnings to stderr for any bad or delisted ticker, so this
  # used to abort the run into the catch block below: no popup, just a log
  # line nobody reads. Exit code 2 is the signal; stderr is only log text.
  $ErrorActionPreference = 'Continue'
  try {
    # A blank stderr line stringifies as 'System.Management.Automation.RemoteException'
    # unless its message is read off directly.
    $output = & $python @checkArgs 2>&1 | ForEach-Object {
      if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
    } | Out-String
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = 'Stop'
    Pop-Location
  }

  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  $logText = '[' + $stamp + '] exit=' + $exitCode + "`r`n" + $output
  [IO.File]::WriteAllText($logPath, $logText, (New-Object Text.UTF8Encoding $false))

  if ($exitCode -eq 2) {
    $text = "Something on your Finviz watchlist was flagged today.`r`n`r`n" +
      "Full report saved to your Desktop\finviz-research folder.`r`n" +
      "Run log: $logPath"
    Show-FinvizPopup -Text $text -Title 'Finviz Research' -IconName 'Information'
  } elseif ($exitCode -ne 0) {
    # something actually broke (not just "nothing flagged today") -- worth a popup too
    $text = "The daily Finviz watchlist check failed (exit code $exitCode).`r`n`r`nLog: $logPath"
    Show-FinvizPopup -Text $text -Title 'Finviz Research -- check failed' -IconName 'Warning'
  }
} catch {
  # Last resort. A log line alone is how scheduled jobs in this repo have died
  # for weeks unnoticed, so this pops a warning too.
  $errMessage = $_.Exception.Message
  try {
    $errText = '[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] SCRIPT ERROR: ' + $errMessage
    [IO.File]::WriteAllText($logPath, $errText, (New-Object Text.UTF8Encoding $false))
  } catch { }
  try {
    $text = "The daily Finviz watchlist check could not run.`r`n`r`n" + $errMessage + "`r`n`r`nLog: $logPath"
    Show-FinvizPopup -Text $text -Title 'Finviz Research -- check failed' -IconName 'Warning'
  } catch { }
}
