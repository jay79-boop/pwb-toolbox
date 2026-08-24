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

# Hours are listed rather than expressed as a repetition interval on one
# trigger. $trigger.Repetition is frequently $null on a freshly created weekly
# trigger, so assigning Interval/Duration through it throws on some machines --
# and it throws at registration time, on theirs, not here. One trigger per hour
# is plainer, is supported everywhere, and shows up in Task Scheduler as eight
# legible rows instead of one rule you have to decode.
$jobs = @(
  @{ Name = 'Premarket'; Job = 'premarket'; Hours = @(7); Minute = 0
     Desc = 'Pre-market gameplan before the open.' },
  @{ Name = 'Alerts';    Job = 'alerts';    Hours = @(9, 10, 11, 12, 13, 14, 15, 16); Minute = 0
     Desc = 'Alert triage each hour through the session.' },
  @{ Name = 'Journal';   Job = 'journal';   Hours = @(16); Minute = 30
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
$source = Join-Path $RepoRoot 'tools\desk_agent\run_job.ps1'
if (-not (Test-Path -LiteralPath $source)) {
  Write-Host ("Runner not found: " + $source)
  Write-Host 'Check out the branch that adds it, then run this again.'
  exit 1
}

# Install a COPY of the launcher outside the repository and point the tasks at
# that, rather than at a path inside a working tree whose branch nobody
# controls. Other sessions switch branches in this checkout all day; a task
# aimed inside it stops existing the moment one of them does. The copy always
# exists, so the launcher always runs, so a bad run always leaves a log --
# which is the difference between a diagnosable failure and silence.
$installDir = Join-Path $env:LOCALAPPDATA 'pwb-desk-agent'
if (-not (Test-Path -LiteralPath $installDir)) {
  New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}
$runner = Join-Path $installDir 'run_job.ps1'
Copy-Item -LiteralPath $source -Destination $runner -Force

# Write the repo location beside the launcher. Without it, a launcher invoked
# with no -RepoRoot walks up from %LOCALAPPDATA% and decides the repository is
# C:\Users\<you>\AppData, which fails in three places at once and none of them
# name the real cause.
[IO.File]::WriteAllText((Join-Path $installDir 'repo_root.txt'), $RepoRoot, (New-Object Text.UTF8Encoding $false))

Write-Host ("Launcher installed at: " + $runner)
Write-Host ("  repo root: " + $RepoRoot + "  (written to repo_root.txt beside it)")
Write-Host ("  copied from " + $source + " -- re-run this script after changing it)")

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

  $arg = '-NoProfile -ExecutionPolicy Bypass -File "' + $runner + '" -Job ' + $j.Job + ' -RepoRoot "' + $RepoRoot + '"'
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
                                    -Argument $arg `
                                    -WorkingDirectory $RepoRoot

  # Build the time from parts rather than parsing a string: a bare '07:00' is
  # handed to the culture's date parser, and this only has to be wrong once on
  # a machine set up differently to be a confusing failure.
  $triggers = @()
  foreach ($hour in $j.Hours) {
    $at = (Get-Date).Date.AddHours($hour).AddMinutes($j.Minute)
    $triggers += New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $at
  }

  try {
    Register-ScheduledTask -TaskName $name `
                           -Action $action `
                           -Trigger $triggers `
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
  $count = @($task.Triggers).Count
  $last  = if ($info -and $info.LastRunTime) { $info.LastRunTime } else { '(never)' }
  $rc    = if ($info) { $info.LastTaskResult } else { '?' }
  Write-Host ("  " + $task.State.ToString().PadRight(9) + " " + $name + "  (" + $count + " trigger(s))")
  Write-Host ("            next run: " + $next)
  Write-Host ("            last run: " + $last + "   result: " + $rc)
  Write-Host ("            StartWhenAvailable: " + $swa)
  # LogonType decides whether this can fire while nobody is signed in. The
  # default from Register-ScheduledTask is Interactive, which means it runs
  # ONLY when the user is logged on -- a 07:00 task on a machine that sleeps
  # overnight then quietly does nothing, which is exactly what happened.
  $logon = $task.Principal.LogonType
  Write-Host ("            LogonType: " + $logon + "   RunAs: " + $task.Principal.UserId)
  if ("$logon" -eq 'Interactive') {
    Write-Host '            NOTE: runs only while you are signed in.'
  }
  if (-not $swa) {
    Write-Host '            WARNING: a run missed while asleep will not catch up.'
  }
  if ("$rc" -eq '267011') {
    Write-Host '            267011 = SCHED_S_TASK_HAS_NOT_RUN: it has never fired.'
  }
  $ok++
}

Write-Host ''
Write-Host ("Registered " + $ok + " of " + $jobs.Count + " tasks.")
Write-Host ''
Write-Host 'To test one right now without waiting for its trigger:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + $runner + '" -Job premarket -RepoRoot "' + $RepoRoot + '"')
Write-Host ''
Write-Host 'To remove them all:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $RepoRoot 'tools\register_desk_agent.ps1') + '" -Remove')
