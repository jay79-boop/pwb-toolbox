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
#
# Enabled = $false keeps a job in this table while taking it off the schedule.
# Deleting the entry instead would leave any already-registered task firing
# forever with nothing here to remove it -- and -Remove would no longer know it
# existed. A job that is off has to still be reachable by the script that turns
# things off.
$jobs = @(
  @{ Name = 'Premarket'; Job = 'premarket'; Hours = @(7); Minute = 0
     Enabled = $true
     Desc = 'Pre-market gameplan before the open.' },

  # OFF since 2026-08-29. Twenty-five consecutive runs, hourly through every
  # session, and not one produced an action: alert_list returned zero on a
  # session proven logged in, because no alerts are configured on the agent's
  # TradingView login. The job asked to be retired or fed in twenty-four
  # successive records and could not raise it any other way -- the weekly
  # review cannot see it, since runlog's dead-job check skips `skipped` runs
  # and this job logs `skipped` correctly whenever nothing fired.
  #
  # Turned off rather than deleted: jobs/alerts.md and the runner's ValidateSet
  # both still carry it, so configuring alerts on that login and flipping this
  # back to $true is the whole of turning it on again.
  @{ Name = 'Alerts';    Job = 'alerts';    Hours = @(9, 10, 11, 12, 13, 14, 15, 16); Minute = 0
     Enabled = $false
     Desc = 'Alert triage each hour through the session.' },

  @{ Name = 'Journal';   Job = 'journal';   Hours = @(16); Minute = 30
     Enabled = $true
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
#
# WakeToRun is NOT the same flag and does not substitute for it. StartWhenAvailable
# catches a missed run up once something else wakes the machine; WakeToRun sets the
# wake timer that makes the machine come up AT the scheduled minute. For these jobs
# only the second one is worth anything: a pre-market gameplan delivered at 10:15
# because that is when the lid was opened is not a pre-market gameplan, and a
# post-close journal entry written the next morning has already lost the day it was
# supposed to capture. Both flags are set, because they fix different halves --
# WakeToRun for a machine asleep, StartWhenAvailable for one that was switched off.
#
# Wake timers can be disabled system-wide in the power plan, in which case this flag
# is accepted, stored, read back as True, and does nothing. That is checked by
# tools/autologon.ps1, not here, because it is a property of the machine rather than
# of the task.
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -WakeToRun `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$registered = @()

foreach ($j in $jobs) {
  $name = $prefix + $j.Name

  # Turning a job off in this file has to turn it off on the machine too.
  # Skipping the entry would leave a previously registered task running on its
  # old schedule while the repo says it is off -- and a schedule you cannot
  # read off the source is how the log stops being trusted. So unregister it
  # every time, and treat "was not there" as success rather than an error.
  if (-not $j.Enabled) {
    try {
      Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
      Write-Host ("turned OFF " + $name + " -- removed the existing task")
    } catch {
      # Do NOT claim the task was absent. Unregister-ScheduledTask throws both
      # when there is no such task and when the removal fails, and those differ
      # by whether an hourly job still fires on Monday. Seen live on
      # 2026-08-29: this printed "was not registered" for a task the previous
      # run had left Ready with 8 triggers, and nothing in the output could say
      # which of the two had happened. The read-back below is what settles it.
      Write-Host ("turned OFF " + $name + " -- no task removed (already absent, or the removal failed)")
    }
    continue
  }

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
$strays = 0
foreach ($j in $jobs) {
  $name = $prefix + $j.Name
  # A job that is off must not read as MISSING -- that is this script's word
  # for a registration that failed, and off-on-purpose is not broken.
  #
  # But ASK WINDOWS; do not just say it. The first version of this branch
  # printed that line straight out of the source file, having checked nothing,
  # which is precisely the mistake this whole script exists to prevent: its own
  # header says the printed line is not evidence and the read-back is. A job
  # turned off here is only really off once Windows agrees, and a stray
  # registration keeps firing on its old schedule with the source claiming
  # otherwise.
  if (-not $j.Enabled) {
    $stray = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($stray) {
      Write-Host ("  STILL ON  " + $name + "  -- disabled here, but WINDOWS STILL HAS IT")
      Write-Host ("            state: " + $stray.State + "   triggers: " + @($stray.Triggers).Count)
      Write-Host '            It will keep firing on its old schedule. Remove it by hand in'
      Write-Host '            Task Scheduler, or run this script with -Remove and then again.'
      $strays++
    } else {
      Write-Host ("  OFF       " + $name + "  -- disabled here, and confirmed gone from Windows")
    }
    continue
  }
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
  # Interactive is CORRECT here and must stay. It is tempting to read "runs only
  # while the user is logged on" as the bug and switch this to S4U or Password so
  # the task fires with nobody signed in -- that was asked for on 2026-08-29, and
  # it is a trap. A task set to run whether the user is logged on or not gets a
  # logon session with NO DESKTOP. Both of these jobs drive TradingView Desktop,
  # an Electron app: premarket reads session levels off the chart and journal
  # captures chart images. They would fire on time and fail at the first chart
  # call -- a job that does not run, converted into a job that runs and is wrong.
  #
  # The fix for "nobody is signed in" is to make the machine sign ITSELF in, so a
  # real desktop exists for these tasks to use. tools/autologon.ps1 reports whether
  # it does, and the note below points there rather than at this flag.
  $logon = $task.Principal.LogonType
  Write-Host ("            LogonType: " + $logon + "   RunAs: " + $task.Principal.UserId)
  if ("$logon" -eq 'Interactive') {
    Write-Host '            NOTE: needs a signed-in desktop -- these jobs drive TradingView.'
    Write-Host '                  Check unattended sign-in with: tools\autologon.ps1'
  }
  if (-not $swa) {
    Write-Host '            WARNING: a run missed while asleep will not catch up.'
  }
  # Read WakeToRun back too. Set above, but the point of this whole block is that
  # what the source asks for and what Windows stored are different questions.
  $wake = $task.Settings.WakeToRun
  Write-Host ("            WakeToRun: " + $wake)
  if (-not $wake) {
    Write-Host '            WARNING: nothing will wake the machine for this run.'
  }
  if ("$rc" -eq '267011') {
    Write-Host '            267011 = SCHED_S_TASK_HAS_NOT_RUN: it has never fired.'
  }
  $ok++
}

$wanted = @($jobs | Where-Object { $_.Enabled }).Count
$off    = $jobs.Count - $wanted

Write-Host ''
Write-Host ("Registered " + $ok + " of " + $wanted + " enabled tasks (" + $off + " turned off).")
if ($strays -gt 0) {
  # Last line, so it cannot scroll off above the summary and be missed.
  Write-Host ("WARNING: " + $strays + " task(s) are turned off here but STILL REGISTERED in Windows.")
  Write-Host '         They will keep firing. This run did not leave the machine in the state'
  Write-Host '         this file describes.'
}
Write-Host ''
Write-Host 'To test one right now without waiting for its trigger:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + $runner + '" -Job premarket -RepoRoot "' + $RepoRoot + '"')
Write-Host ''
Write-Host 'To remove them all:'
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $RepoRoot 'tools\register_desk_agent.ps1') + '" -Remove')
