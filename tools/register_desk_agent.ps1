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

  Each job in the table below declares whether it NEEDS A DESKTOP, and that
  one field decides how its task is registered. A job that drives TradingView
  Desktop is registered Interactive and only runs while somebody is signed in,
  because an Electron app must have somewhere to draw. A job that reads its
  levels from bar data is registered against a stored credential and runs
  whether anyone is signed in or not. Supplying the password is prompted for
  once, and -NoStoredCredential declines it.

.PARAMETER RepoRoot
  The checkout the tasks should run in. Defaults to the OneDrive checkout,
  which is the canonical one.

.PARAMETER Remove
  Unregister the tasks instead of creating them.

.PARAMETER TaskUser
  The account a desktop-free task runs as. Defaults to the current user, whose
  OneDrive holds both the repository and the trade journal.

.PARAMETER NoStoredCredential
  Register every task Interactive, even the ones that need no desktop. They
  then work exactly as before and need the machine signed in.

.EXAMPLE
  .\tools\register_desk_agent.ps1

.EXAMPLE
  .\tools\register_desk_agent.ps1 -Remove
#>
[CmdletBinding()]
param(
  [string] $RepoRoot = 'C:\Users\Gexio\OneDrive\pwb-toolbox',
  [switch] $Remove,

  # Who a desktop-free task runs as. Defaults to the current user, which is the
  # account whose OneDrive holds both the repository and the trade journal.
  [string] $TaskUser = ("$env:USERDOMAIN\$env:USERNAME"),

  # Register the desktop-free tasks with Interactive anyway. For a machine that
  # is always signed in and where supplying a password is not wanted -- the
  # jobs still work, they just go back to needing a live desktop session.
  [switch] $NoStoredCredential
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
     NeedsDesktop = $false
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
     NeedsDesktop = $true
     Desc = 'Alert triage each hour through the session.' },

  @{ Name = 'Journal';   Job = 'journal';   Hours = @(16); Minute = 30
     Enabled = $true
     NeedsDesktop = $false
     Desc = 'Capture the day into the trade journal after the close.' }
)

# NeedsDesktop is the field that decides the LogonType, and it is the whole
# point of this file since 2026-08-29.
#
# A task set to run whether the user is logged on or not gets a logon session
# with NO DESKTOP. While every job drove TradingView Desktop -- an Electron app
# -- that made the desktopless logon types strictly worse than a task that
# never fires: a run that fails at the first chart call still produces a
# gameplan, on time, looking exactly like a real one. That is the argument in
# docs/decisions/2026-08-29-the-logon-type-is-not-the-bug.md and it still holds
# for any job left in the $true column.
#
# `premarket` and `journal` no longer read a chart. They get their levels from
# bar data through tools/desk_levels.py and render their own images headless,
# so they have nothing to render into a desktop and can take a logon type that
# has none. `alerts` still drives the chart, and stays.
#
# Password rather than S4U, deliberately. S4U stores no password, which reads
# as the safer option, but it also carries NO CREDENTIALS: DPAPI-protected
# secrets do not decrypt and network paths are not reachable as the user. Both
# of these jobs run Claude Code, whose own stored authentication is exactly
# that kind of secret, and the journal job reads a document under OneDrive. S4U
# would fail both -- and fail them at run time, unattended, in a way that looks
# like a broken agent rather than a wrong logon type.

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

# Say which commit every moving part came from. Until this existed, a run
# printed a summary line and nothing on it named the version that produced it,
# so on 2026-08-29 a run against a stale checkout was caught only by somebody
# recognising the wording of its own output -- and the diagnosis stayed
# unproven afterwards, because the evidence had never been printed.
#
# Deliberately NOT an ahead/behind count against a remote. That compares two
# refs rather than two working trees, and reads "up to date" whenever the
# checkout sits on a branch that already contains the ref it is compared with.
# CLAUDE.md carries that trap; a per-file commit and date sidesteps it.
function Get-Git {
  param([string] $Root, [string[]] $GitArgs)
  try {
    $out = & git -C $Root @GitArgs 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return ("$out").Trim()
  } catch {
    return ''   # git is not on PATH; every caller below degrades to a note
  }
}

function Get-FileVersion {
  param([string] $Root, [string] $RelPath)
  $stamp = Get-Git $Root @('log', '-1', '--format=%h %ad', '--date=short', '--', $RelPath)
  if (-not $stamp) { return '(no commit found -- not a git checkout?)' }
  # A commit id is a lie about a file with uncommitted edits sitting on top of
  # it, and that is exactly the state a half-finished session leaves behind.
  if (Get-Git $Root @('status', '--porcelain', '--', $RelPath)) {
    return ($stamp + '  EDITED, not committed')
  }
  return $stamp
}

$branch  = Get-Git $RepoRoot @('rev-parse', '--abbrev-ref', 'HEAD')
$head    = Get-Git $RepoRoot @('rev-parse', '--short', 'HEAD')
$version = if ($head) { $branch + ' @ ' + $head } else { '(not a git checkout)' }

# Which copy of THIS script is running is a different question from which
# checkout the tasks will use. A second checkout exists at
# C:\Users\Gexio\pwb-toolbox, so running one checkout's script against the
# other's -RepoRoot is a real way to read the wrong file's version and believe
# it. Report the running file from its own tree, and name that tree when the
# two differ.
$selfRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { $RepoRoot }
# A trailing backslash on -RepoRoot is the one difference that is not a
# difference; string comparison is already case-insensitive here.
$sameTree = $selfRoot.TrimEnd('\') -eq $RepoRoot.TrimEnd('\')

Write-Host ("Launcher installed at: " + $runner)
Write-Host ("  repo root: " + $RepoRoot + "  (written to repo_root.txt beside it)")
Write-Host ("  checkout:  " + $version)
Write-Host ("  this script:  " + (Get-FileVersion $selfRoot 'tools/register_desk_agent.ps1'))
Write-Host ("  launcher:     " + (Get-FileVersion $RepoRoot 'tools/desk_agent/run_job.ps1'))
Write-Host ("  copied from " + $source + " -- re-run this script after changing it")
if (-not $sameTree) {
  Write-Host ("  WARNING: this script is running out of " + $selfRoot + ", which is not the repo root above.")
  Write-Host '           Those are two different checkouts and they can hold different versions.'
}

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

# Ask for the password ONCE, and only when a desktop-free job is actually being
# registered. A machine that only runs chart jobs is never prompted, and
# -NoStoredCredential opts out entirely at the cost of going back to needing a
# live desktop.
#
# Read-Host -AsSecureString rather than a parameter: a password on the command
# line goes into the PowerShell history file in clear text, and a hand-edited
# command is how a password ends up pasted into a chat window.
$deskFree = @($jobs | Where-Object { $_.Enabled -and -not $_.NeedsDesktop })
$taskPassword = $null
if ($deskFree.Count -gt 0 -and -not $NoStoredCredential) {
  Write-Host ''
  Write-Host ('These jobs need no desktop and can run while nobody is signed in: ' +
              (($deskFree | ForEach-Object { $_.Job }) -join ', '))
  Write-Host ("Windows stores the password for them as an LSA secret, against " + $TaskUser + ".")
  Write-Host ''
  Write-Host 'IF YOU SIGN IN WITH A PIN there may be no account password at all. A PIN is a'
  Write-Host 'device-local credential sealed in the TPM, not the account password, and on a'
  Write-Host 'passwordless setup Windows never created one. That is expected, and it is NOT'
  Write-Host 'a dead end -- there is a second route that needs no password:'
  Write-Host ''
  Write-Host '  Press ENTER here to register these Interactive, then turn on ARSO:'
  Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\autologon.ps1'
  Write-Host ''
  Write-Host '  ARSO signs you back in after a restart and locks the device immediately, so'
  Write-Host '  the session these tasks need is recreated without any password existing.'
  Write-Host '  Interactive + ARSO is a COMPLETE setup, not a half-finished one.'
  Write-Host ''
  $secure = Read-Host -Prompt ("Windows password for " + $TaskUser) -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    $taskPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
  } finally {
    # Zero the unmanaged copy whatever happened above. The managed string below
    # is unavoidable -- Register-ScheduledTask takes a plain [string] -- but
    # there is no reason to leave a second copy lying in unmanaged memory.
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
  if ([string]::IsNullOrEmpty($taskPassword)) {
    $taskPassword = $null
    Write-Host 'No password given: registering everything Interactive.'
  }
  Write-Host ''
}

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

  # THE BRANCH THIS WHOLE FILE TURNS ON.
  #
  # A job that needs a desktop is registered with no credentials at all, which
  # leaves Windows' own default of Interactive -- it then runs only while
  # somebody is signed in, which for a job that has to render an Electron
  # window is not a limitation but a requirement.
  #
  # A job that needs no desktop is registered against a stored credential, and
  # runs whether anyone is signed in or not. Note that -User and -Password
  # together are what select the Password logon type; there is no -LogonType
  # here, and passing one would be a second way to say the same thing.
  $common = @{
    TaskName    = $name
    Action      = $action
    Trigger     = $triggers
    Settings    = $settings
    Description = $j.Desc
    Force       = $true
  }
  if ($j.NeedsDesktop -or -not $taskPassword) {
    $how = 'Interactive (needs you signed in)'
  } else {
    $common['User'] = $TaskUser
    $common['Password'] = $taskPassword
    $how = 'stored credential, no desktop needed'
  }

  try {
    Register-ScheduledTask @common | Out-Null
  } catch {
    Write-Host ("FAILED to register " + $name + ": " + $_.Exception.Message)
    # Name the likely cause rather than leaving the raw COM message alone. A
    # wrong password fails here and nowhere else, and the message Windows
    # returns for it does not say "password".
    if ($common.ContainsKey('Password')) {
      Write-Host ('           ' + $name + ' was being registered against ' + $TaskUser + '.')
      Write-Host '           If that account or password is wrong, this is where it shows up.'
      Write-Host '           Re-run with -NoStoredCredential to register it Interactive instead.'
    }
    continue
  }
  Write-Host ("registered " + $name + " -- " + $how)
  $registered += $name
}

# Read every task back. This is the only proof that registration worked.
Write-Host ''
Write-Host 'Reading the tasks back from Windows:'
Write-Host ''
$ok = 0
$strays = 0
# LastTaskResult is only the script's exit code once a run has ENDED. In between,
# Windows parks a status code there instead, from either of two families, and
# neither is explained by the console:
#
#   0x000413xx  the SCHED_S_* states. All SUCCESS HRESULTs, so a six-digit
#               number here is a state, not a failure.
#   0x8007xxxx  a Win32 error, and 0xC00000xx an NT status. These ten-digit
#               numbers ARE failures.
#
# The three that matter most all look like nothing is wrong. 267011: never once
# fired. 267009: a run is still in flight -- and while it is, Windows SKIPS every
# later occurrence rather than starting a second copy. 2147946720: the mirror
# image, a firing that was refused because the previous run had not ended. One
# hung run therefore turns a schedule off with no error anywhere, and shows up as
# 267009 here and 2147946720 on the next look. Decode them all.
$resultCodes = @{
  '267008' = '267008 = SCHED_S_TASK_READY: finished, waiting for its next run.'
  '267009' = '267009 = SCHED_S_TASK_RUNNING: a run is in flight RIGHT NOW.'
  '267010' = '267010 = SCHED_S_TASK_DISABLED: registered, but it will not fire.'
  '267011' = '267011 = SCHED_S_TASK_HAS_NOT_RUN: it has never fired.'
  '267012' = '267012 = SCHED_S_TASK_NO_MORE_RUNS: nothing left on the schedule.'
  '267013' = '267013 = SCHED_S_TASK_NOT_SCHEDULED: no trigger set to run it.'
  '267014' = '267014 = SCHED_S_TASK_TERMINATED: the last run was killed.'
  '267015' = '267015 = SCHED_S_TASK_NO_VALID_TRIGGERS: triggers missing or off.'
  '267045' = '267045 = SCHED_S_TASK_QUEUED: waiting for an earlier run to end.'
  '2147946720' = '2147946720 = 0x800710E0: Windows REFUSED to start this firing.'
  '3221225786' = '3221225786 = 0xC000013A: the console was closed with Ctrl+C.'
}
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
  # The right LogonType is now a per-job question, and the read-back has to ask
  # it per job or it reports a successful conversion as a fault.
  #
  # For a job that still drives TradingView, Interactive is CORRECT and must
  # stay. Reading "runs only while the user is logged on" as the bug and
  # switching it to S4U or Password is the trap of 2026-08-29: such a task gets
  # a logon session with NO DESKTOP, so an Electron app has nowhere to render.
  # It would fire on time and fail at the first chart call -- a job that does
  # not run, converted into a job that runs and is wrong.
  #
  # For a job that reads its levels from bar data there is no desktop to lose,
  # and Interactive is now the weaker setting rather than the safe one: it is
  # the only reason such a task still needs the machine signed in at all.
  $logon = $task.Principal.LogonType
  Write-Host ("            LogonType: " + $logon + "   RunAs: " + $task.Principal.UserId)
  $desktopless = @('S4U', 'Password') -contains "$logon"
  if ($j.NeedsDesktop) {
    if ($desktopless) {
      Write-Host '            WRONG: this job drives TradingView and has no desktop to draw in.'
      Write-Host '                   It will fire on time and fail at the first chart call.'
      Write-Host '                   Re-register it, or set it back to Interactive by hand.'
    } else {
      Write-Host '            NOTE: needs a signed-in desktop -- this job drives TradingView.'
      Write-Host '                  Check unattended sign-in with: tools\autologon.ps1'
    }
  } elseif ($desktopless) {
    Write-Host '            GOOD: no desktop needed, and none required. Runs signed in or not.'
  } else {
    # TWO ways to be finished here, and naming only one of them sent a PIN user
    # after a password that does not exist. Say both.
    Write-Host '            OK: this job needs no desktop, but is registered Interactive,'
    Write-Host '                so a signed-in session has to exist when it fires. Two ways'
    Write-Host '                to settle that, and either is a complete answer:'
    Write-Host '                  - give this script the account password, so the task'
    Write-Host '                    needs no session at all; or'
    Write-Host '                  - turn on ARSO, so a restart recreates the session.'
    Write-Host '                    No password needed: tools\autologon.ps1'
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
  if ($resultCodes.ContainsKey("$rc")) {
    Write-Host ('            ' + $resultCodes["$rc"])
  }
  if ("$rc" -eq '267009') {
    Write-Host '            A run that stays RUNNING is hung. Windows will skip every'
    Write-Host '            later occurrence until it ends, so the job goes quiet with'
    Write-Host '            no error. End it in Task Scheduler, then read its log.'
  }
  if ("$rc" -eq '2147946720') {
    Write-Host '            For a batch job like these that means the PREVIOUS run had'
    Write-Host '            not ended, so this firing never started. Same hung schedule'
    Write-Host '            as 267009, seen from the next occurrence.'
    Write-Host '            (On a long-lived keep-alive task it is normal and healthy:'
    Write-Host '            the repeating trigger bouncing off the instance already up.)'
  }
  $ok++
}

$wanted = @($jobs | Where-Object { $_.Enabled }).Count
$off    = $jobs.Count - $wanted

Write-Host ''
# The version rides on the summary line as well as appearing at the top,
# because the summary is the line that gets read at a glance and pasted back.
Write-Host ("Registered " + $ok + " of " + $wanted + " enabled tasks (" + $off + " turned off), from " + $version + ".")
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
