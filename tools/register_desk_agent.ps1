<#
.SYNOPSIS
  Register the Windows scheduled tasks that run the desk agent unattended.

.DESCRIPTION
  Creates one task per scheduled job and then reads each one back, because
  Register-ScheduledTask can fail while the surrounding script still prints
  that it succeeded -- the printed line is not evidence, the read-back is.

  Only a Windows scheduled task is proven to fire unattended on this machine.
  Claude Code's own scheduled-task folders have no Windows task behind them,
  and an in-session cron dies with the session.

  The weekly review is deliberately not registered here: it needs neither
  TradingView nor the local disk, so it runs as a cloud Routine instead. See
  tools/desk_agent/README.md.

.PARAMETER RepoRoot
  The checkout the tasks should run in. Defaults to the OneDrive checkout,
  which is the canonical one.

.PARAMETER Remove
  Unregister the tasks instead of creating them.

.EXAMPLE
  .\tools\register_desk_agent.ps1

.EXAMPLE
  .\tools\register_desk_agent.ps1 -Remove
#>
[CmdletBinding()]
param(
  [string] $RepoRoot = 'C:\Users\Gexio\OneDrive\pwb-toolbox',
  [switch] $Remove
)

$ErrorActionPreference = 'Stop'

$prefix = 'PWB-DeskAgent-'
$jobs = @(
  @{ Name = 'Premarket'; Job = 'premarket'; At = '07:00'; Repeat = $false
     Desc = 'Pre-market gameplan before the open.' },
  @{ Name = 'Alerts';    Job = 'alerts';    At = '09:00'; Repeat = $true
     Desc = 'Hourly alert triage during market hours.' },
  @{ Name = 'Journal';   Job = 'journal';   At = '16:30'; Repeat = $false
     Desc = 'Capture the day into the trade journal after the close.' }
)

if ($Remove) {
  foreach ($j in $jobs) {
    $name = $prefix + $j.Name
    try {
      Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
      Write-Host ("removed " + $name)
    } catch {
      Write-Host ("not present: " + $name)
    }
  }
  Write-Host ''
  Write-Host 'Done. Nothing else on the machine was changed.'
  exit 0
}

if (-not (Test-Path -LiteralPath $RepoRoot)) {
  Write-Host ("Repo root not found: " + $RepoRoot)
  Write-Host 'Pass -RepoRoot with the checkout you want the agent to run in.'
  exit 1
}
$runner = Join-Path $RepoRoot 'tools\desk_agent\run_job.ps1'
if (-not (Test-Path -LiteralPath $runner)) {
  Write-Host ("Runner not found: " + $runner)
  Write-Host 'Check out the branch that adds it, then run this again.'
  exit 1
}

# StartWhenAvailable is the flag whose absence silently eats overnight runs on a
# machine that was asleep at the scheduled minute. AllowStartIfOnBatteries and
# DontStopIfGoingOnBatteries keep a laptop run from being killed mid-job.
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$registered = @()

foreach ($j in $jobs) {
  $name = $prefix + $j.Name

  $arg = '-NoProfile -ExecutionPolicy Bypass -File "' + $runner + '" -Job ' + $j.Job
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
                                    -Argument $arg `
                                    -WorkingDirectory $RepoRoot

  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $j.At
  if ($j.Repeat) {
    # Hourly through the session. Set on the trigger object rather than at
    # creation: the -RepetitionInterval parameter is not on the weekly form.
    $trigger.Repetition.Interval = 'PT1H'
    $trigger.Repetition.Duration = 'PT7H'
  }

  try {
    Register-ScheduledTask -TaskName $name `
                           -Action $action `
                           -Trigger $trigger `
                           -Settings $settings `
                           -Description $j.Desc `
                           -Force | Out-Null
  } catch {
    Write-Host ("FAILED to register " + $name + ": " + $_.Exception.Message)
    continue
  }
  $registered += $name
}

# Read every task back. This is the only proof that registration worked.
Write-Host ''
Write-Host 'Reading the tasks back from Windows:'
Write-Host ''
$ok = 0
foreach ($j in $jobs) {
  $name = $prefix + $j.Name
  $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $task) {
    Write-Host ("  MISSING   " + $name + "  -- not registered")
    continue
  }
  $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
  $next = if ($info -and $info.NextRunTime) { $info.NextRunTime } else { '(not scheduled)' }
  $swa = $task.Settings.StartWhenAvailable
  Write-Host ("  OK        " + $name)
  Write-Host ("            next run: " + $next)
  Write-Host ("            StartWhenAvailable: " + $swa)
  if (-not $swa) {
    Write-Host '            WARNING: a run missed while asleep will not catch up.'
  }
  $ok++
}

Write-Host ''
Write-Host ("Registered " + $ok + " of " + $jobs.Count + " tasks.")
Write-Host ''
Write-Host 'To test one right now without waiting for its trigger:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + $runner + '" -Job premarket')
Write-Host ''
Write-Host 'To remove them all:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $RepoRoot 'tools\register_desk_agent.ps1') + '" -Remove')
