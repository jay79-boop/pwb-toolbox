<#
.SYNOPSIS
  Register a Windows Scheduled Task that runs the Finviz watchlist check
  once a day and pops a message if anything was flagged. Optional -- the
  menu's own "Check your watchlist" option needs no install and covers the
  on-demand case; this is for "check it for me automatically."

.DESCRIPTION
  Registered Interactive (needs you signed in), not against a stored
  credential: the whole point is a popup, and a popup needs a desktop to
  draw on -- the same tradeoff tools\desk_agent's 'alerts' job makes for
  the same reason (see register_desk_agent.ps1's own comment on
  NeedsDesktop). If the machine is not signed in at the scheduled time, the
  check simply does not run that day. StartWhenAvailable/WakeToRun (making
  a missed run catch up, or waking a sleeping machine) are deliberately
  left out of this first version to keep it simple -- ask if you want
  either added.

  Idempotent: re-running replaces the existing task rather than
  duplicating it. The task is read back from Windows afterwards, because a
  printed "registered" line is not proof it actually took.

  Windows PowerShell 5.1: ASCII bytes only, no bash-style command
  chaining, no '~' for home, no here-strings.

.PARAMETER Hour
  24-hour local time to run at. Default 8 (8:00 AM) -- after most
  premarket moves are visible, before most people are deep into their day.

.PARAMETER Remove
  Unregister the task instead of creating it.

.EXAMPLE
  .\tools\install_finviz_watchlist_task.ps1

.EXAMPLE
  .\tools\install_finviz_watchlist_task.ps1 -Hour 7

.EXAMPLE
  .\tools\install_finviz_watchlist_task.ps1 -Remove
#>
[CmdletBinding()]
param(
  [ValidateRange(0, 23)]
  [int] $Hour = 8,

  [switch] $Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'Finviz-WatchlistCheck'

if ($Remove) {
  try {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed $taskName"
  } catch {
    Write-Host "Nothing to remove: $taskName was not registered."
  }
  exit 0
}

$alertScript = Join-Path $PSScriptRoot 'finviz_watchlist_alert.ps1'
if (-not (Test-Path -LiteralPath $alertScript)) {
  throw "finviz_watchlist_alert.ps1 is not next to this script. Expected: $alertScript"
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powershell)) {
  $found = Get-Command powershell.exe -ErrorAction SilentlyContinue
  if (-not $found) { throw 'Could not find powershell.exe on this computer.' }
  $powershell = $found.Source
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $alertScript + '"'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $repoRoot

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$at = (Get-Date).Date.AddHours($Hour)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $at

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

try {
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Daily Finviz watchlist timing-signal check.' `
    -Force | Out-Null
} catch {
  throw "Register-ScheduledTask failed: $($_.Exception.Message)"
}

# Read it back. A printed "registered" line is not proof -- see
# register_desk_agent.ps1's own comment on exactly this trap.
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
  throw "Registration did not take: $taskName is not listed by Get-ScheduledTask."
}
$info = Get-ScheduledTaskInfo -TaskName $taskName

Write-Host ''
Write-Host "Registered: $taskName" -ForegroundColor Green
Write-Host ("  runs weekdays at {0:00}:00, next run: {1}" -f $Hour, $info.NextRunTime)
Write-Host "  logon type: $($task.Principal.LogonType)  (Interactive -- needs you signed in; a popup needs a desktop to draw on)"
Write-Host ''
Write-Host 'This only pops a message when something is actually flagged -- a quiet day means nothing showed up.'
Write-Host 'Every run (flagged or not) is logged to: tools\finviz_scan_watchlist_task.log'
Write-Host ''
Write-Host 'To test it right now without waiting for the scheduled time:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + $alertScript + '"')
Write-Host ''
Write-Host 'To remove it:'
Write-Host '  .\tools\install_finviz_watchlist_task.ps1 -Remove'
exit 0
