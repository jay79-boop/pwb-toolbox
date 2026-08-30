<#
.SYNOPSIS
  Report whether this machine can run the desk agent with nobody signed in.

.DESCRIPTION
  WHICH JOBS STILL NEED THIS IS NOW A PER-JOB QUESTION, and answering it for
  the whole agent at once is how this report would call a successful conversion
  a fault.

  A scheduled task that runs whether the user is logged on or not gets a logon
  session with NO DESKTOP. That is fatal to a job that drives TradingView
  Desktop, an Electron app with nowhere to render: it would fire on time and
  fail at the first chart call, producing a gameplan built on whatever the
  failure left behind. A job that does not run is at least honest about it.
  That was the whole argument in
  docs/decisions/2026-08-29-the-logon-type-is-not-the-bug.md, and for a chart
  job it still stands.

  Since 2026-08-29, premarket and journal do not read a chart. They take their
  session levels, prior-day range and fair value gaps from bar data through
  tools/desk_levels.py and render their own images headless, so their tasks CAN
  be registered against a stored credential and then need no desktop and no
  sign-in. Only `alerts`, which is currently OFF, still drives the chart.

  CAN, not ARE, and that word is the bug this script shipped with. Needing no
  desktop and carrying a stored credential are two facts, not one: the password
  prompt is declinable, and declining leaves a job that needs no chart still
  registered Interactive and still waiting for a sign-in. The first real run,
  on 2026-08-30, declined it -- and this report announced "nothing needs
  signing in" about two Interactive tasks while section 3 said the opposite a
  few lines below. So the two facts are now computed separately and only the
  second one is allowed to retire the sign-in.

  So this script reports two different things:

    * For a task that needs a desktop -- automatic sign-in has to work, and the
      three conditions below all have to be true.
    * For a task that does not -- it should NOT be Interactive, because that is
      then the only thing still tying it to a signed-in machine.

  The three conditions, when a desktop job exists:

    1. the machine signs in without a human      -- AutoAdminLogon
    2. the machine is awake at the scheduled minute -- WakeToRun, and wake
                                                    timers enabled in the power plan
    3. the password is not sitting in the registry in plaintext

  This script reads all three back and says which are missing. It does not set
  up the sign-in itself: storing the password correctly means writing an LSA
  secret, and Sysinternals Autologon already does that properly and is
  published by Microsoft. Rolling our own here would be untested P/Invoke
  against the credential store, which is the last place to put code nobody can
  run before shipping it.

  It stops treating a missing sign-in as a problem only when every registered
  task actually carries a stored credential -- not merely when no job needs a
  chart. Until then the sign-in is still what starts them, and saying otherwise
  would be this script telling a comfortable story about a machine it had just
  finished reading correctly.

.PARAMETER EnableLock
  Register a task that locks the workstation shortly after every logon, so an
  unattended reboot does not leave the desktop sitting open.

  Read both caveats before turning this on. It fires on EVERY logon, including
  yours -- you sign in, and half a minute later the screen locks. And whether
  TradingView still renders a chart for capture on a locked session is NOT
  established: applications keep running and CDP draws from the compositor
  rather than the screen, so it should hold, but Chromium throttles occluded
  windows and nobody has proven it here. If journal screenshots start coming
  back blank, this switch is the first thing to undo.

.PARAMETER DisableLock
  Remove that task.

.EXAMPLE
  .\tools\autologon.ps1

.EXAMPLE
  .\tools\autologon.ps1 -EnableLock
#>
[CmdletBinding()]
param(
  [switch] $EnableLock,
  [switch] $DisableLock
)

$ErrorActionPreference = 'Stop'

$winlogon     = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$lockTaskName = 'PWB-LockOnLogon'
$agentPrefix  = 'PWB-DeskAgent-'
$wakeTimerGuid = 'bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d'

# Task-name suffixes whose job still drives TradingView Desktop, and therefore
# still needs a real desktop to render into. This is the ONLY reason anything
# in this file exists, so getting it wrong is how the report starts lying.
#
# It has to be repeated here rather than read from register_desk_agent.ps1's
# $jobs table: this script is run on its own, often from a different directory,
# and a report that fails when it cannot find another file is worse than one
# carrying a short list. tests/test_desk_agent_launcher.py asserts this list
# agrees with that table, which is the same arrangement the job names already
# have across three files.
$desktopTasks = @('Alerts')

function Get-PolicyValue([string] $name) {
  # ARSO's policy lives under Policies\System, NOT under Winlogon where the
  # rest of the sign-in values are. Two keys, and reading the wrong one reports
  # "not set" for a policy that is switched on -- the shape of wrong answer this
  # script exists to avoid.
  $key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
  $item = Get-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue
  if ($null -eq $item) { return $null }
  return $item.$name
}

function Get-WinlogonValue([string] $name) {
  # -ErrorAction SilentlyContinue on Get-ItemProperty returns $null for a
  # missing value AND for a missing key, which is the answer we want in both
  # cases: not configured.
  $item = Get-ItemProperty -Path $winlogon -Name $name -ErrorAction SilentlyContinue
  if ($null -eq $item) { return $null }
  return $item.$name
}

# -- the two write actions -----------------------------------------------------

if ($EnableLock -and $DisableLock) {
  Write-Host 'Pass one of -EnableLock or -DisableLock, not both.'
  exit 1
}

if ($DisableLock) {
  try {
    Unregister-ScheduledTask -TaskName $lockTaskName -Confirm:$false -ErrorAction Stop
    Write-Host ('removed ' + $lockTaskName)
  } catch {
    # Same trap as register_desk_agent.ps1: Unregister-ScheduledTask throws
    # both when the task is absent and when the removal fails. Do not report
    # one of those as though it were the other -- ask Windows instead.
    Write-Host ('no task removed (already absent, or the removal failed)')
  }
  if (Get-ScheduledTask -TaskName $lockTaskName -ErrorAction SilentlyContinue) {
    Write-Host ('STILL THERE: ' + $lockTaskName + ' is still registered. Remove it in Task Scheduler.')
    exit 1
  }
  Write-Host ('confirmed gone: ' + $lockTaskName)
  exit 0
}

if ($EnableLock) {
  $me = $env:USERDOMAIN + '\' + $env:USERNAME
  $action = New-ScheduledTaskAction -Execute 'rundll32.exe' -Argument 'user32.dll,LockWorkStation'
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
  # Lock a little after the shell comes up rather than racing it. A lock fired
  # into a half-built session has been known to land before the desktop exists
  # and do nothing at all.
  try { $trigger.Delay = 'PT30S' } catch {
    Write-Host 'note: could not set the 30s delay on the trigger; it will lock immediately.'
  }
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  try {
    Register-ScheduledTask -TaskName $lockTaskName `
                           -Action $action `
                           -Trigger $trigger `
                           -Settings $settings `
                           -Description 'Lock the workstation after logon, so an unattended reboot does not leave the desktop open.' `
                           -Force | Out-Null
  } catch {
    Write-Host ('FAILED to register ' + $lockTaskName + ': ' + $_.Exception.Message)
    exit 1
  }
  # Read it back. The printed line is not evidence.
  $lock = Get-ScheduledTask -TaskName $lockTaskName -ErrorAction SilentlyContinue
  if (-not $lock) {
    Write-Host ('FAILED: ' + $lockTaskName + ' did not register, despite no error above.')
    exit 1
  }
  Write-Host ('registered ' + $lockTaskName + '  state: ' + $lock.State)
  Write-Host 'It fires on EVERY logon, yours included. Undo with: .\tools\autologon.ps1 -DisableLock'
  exit 0
}

# -- the report ----------------------------------------------------------------

Write-Host ''
Write-Host 'Can this machine run the desk agent with nobody signed in?'
Write-Host ''

$problems = 0

# TWO facts, and the first run of this script on the real machine proved they
# are not the same one.
#
#   $desktopNeeded    -- does any registered task still DRIVE THE CHART?
#   $signInMatters    -- does any registered task still DEPEND ON A SIGN-IN?
#
# The first version computed only the first and let sections 1 and the summary
# speak for the second. On 2026-08-30 that printed "every registered task runs
# on a stored credential" and "nothing needs signing in" about two tasks that
# were registered Interactive, while section 3 correctly said the opposite four
# lines further down. The password prompt had been declined, which is a
# supported answer -- so the report was describing the conversion it offered
# rather than the machine in front of it.
#
# They come apart exactly when a job needs no desktop but is still registered
# Interactive, which is the DEFAULT state and the state after declining the
# prompt. A job that needs no desktop is not thereby free of the sign-in; it is
# only free of it once it actually carries a stored credential.
$agentTasks = @(Get-ScheduledTask -TaskName ($agentPrefix + '*') -ErrorAction SilentlyContinue)
$desktopNeeded = $false
$signInMatters = $false
foreach ($t in $agentTasks) {
  $suffix = $t.TaskName.Substring($agentPrefix.Length)
  $wantsDesktop = $desktopTasks -contains $suffix
  # Same test section 3 uses, deliberately: one definition of "runs without a
  # desktop", so the two halves of this report cannot drift apart again.
  $desktopless = @('S4U', 'Password') -contains "$($t.Principal.LogonType)"
  if ($wantsDesktop) { $desktopNeeded = $true }
  if ($wantsDesktop -or -not $desktopless) { $signInMatters = $true }
}
if ($agentTasks.Count -gt 0 -and -not $signInMatters) {
  Write-Host 'No registered task needs a desktop, and every one of them carries a stored'
  Write-Host 'credential. Signing in after a restart is therefore optional here: sections 1'
  Write-Host 'and 2 are reported for information and are not counted as problems.'
  Write-Host ''
} elseif ($agentTasks.Count -gt 0 -and -not $desktopNeeded) {
  Write-Host 'No registered task needs a desktop -- but they are registered Interactive, so'
  Write-Host 'they still only run while you are signed in. Sections 1 and 2 therefore still'
  Write-Host 'apply. Section 3 names which tasks, and how to lift it.'
  Write-Host ''
}

# 1. Does it sign itself in? There are TWO routes and they are not equivalent.
#
# The first version of this script knew only about AutoAdminLogon and told
# anyone without it to run Sysinternals Autologon. That advice is WRONG for this
# machine and was corrected on 2026-08-29 when the owner said they sign in with a
# PIN: a Windows Hello PIN is a device-local credential sealed in the TPM, not
# the account password, and Autologon needs the password. Following that advice
# would have sent them hunting for a Microsoft-account password they had never
# typed, to enable something they do not need.
#
# ARSO is the route that fits a PIN, and it is the better one regardless: it
# signs the last user back in after a restart or cold boot, rehydrates the
# session, AND LOCKS IT immediately -- so the desktop the scheduled tasks need
# exists, with no password stored anywhere and no open desktop left behind.
#
# Since 2026-08-29 that argument only binds the jobs that still drive the chart.
# premarket and journal read their levels from bar data and run on a stored
# credential, so on a machine where alerts is off NOTHING below is a fault --
# it is reported so the state is visible, not because anything depends on it.
$arsoPolicy = Get-PolicyValue 'DisableAutomaticRestartSignOn'
$auto   = Get-WinlogonValue 'AutoAdminLogon'
$user   = Get-WinlogonValue 'DefaultUserName'
$domain = Get-WinlogonValue 'DefaultDomainName'
$plain  = Get-WinlogonValue 'DefaultPassword'

Write-Host '1. Signing in after a restart'
Write-Host ''
Write-Host '   Route A -- ARSO, the one that works with a PIN. Recommended.'
if ("$arsoPolicy" -eq '1') {
  Write-Host '     BLOCKED  DisableAutomaticRestartSignOn=1: a policy turns ARSO off outright.'
  Write-Host '              The Settings toggle cannot override this. Clear it in an ADMIN'
  Write-Host '              PowerShell, then set the toggle:'
  Write-Host "                Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name DisableAutomaticRestartSignOn"
  if ($desktopNeeded) {
    $problems++
  } else {
    Write-Host '              Not counted as a problem here: no registered task needs a'
    Write-Host '              desktop, so nothing depends on this machine signing itself in.'
  }
} else {
  $arsoShown = if ($null -eq $arsoPolicy) { 'not set' } else { $arsoPolicy }
  Write-Host ('     not blocked by policy (DisableAutomaticRestartSignOn=' + $arsoShown + ')')
  # Deliberately NOT reported as "on". The per-user consent behind the Settings
  # toggle is not reliably readable from here, and this script's whole premise
  # is that a printed line is not evidence. Saying where to look beats asserting
  # a state that was never checked -- which is the exact bug fixed in #154.
  Write-Host '     The toggle itself is per-user and this script cannot read it. Confirm it at:'
  Write-Host '       Settings > Accounts > Sign-in options > Additional settings >'
  Write-Host '       "Use my sign-in info to automatically finish setting up after an update"'
  Write-Host '     Real proof is the next 07:00 run appearing in the log after an overnight'
  Write-Host '     reboot. Nothing readable here can stand in for that.'
}

Write-Host ''
Write-Host '   Route B -- full autologon. Needs the account PASSWORD, not a PIN.'
if ("$auto" -eq '1') {
  Write-Host ('     ON     AutoAdminLogon=1, user: ' + $domain + '\' + $user)
} else {
  Write-Host ('     off    AutoAdminLogon=' + $(if ($null -eq $auto) { '(not set)' } else { $auto }))
  Write-Host '     Not a problem if Route A is on -- this one boots to an UNLOCKED desktop,'
  Write-Host '     so anyone who powers the machine on is inside it. Only worth it if ARSO'
  Write-Host '     cannot be made to work. It needs the real account password (a Microsoft'
  Write-Host '     account password is resettable at account.live.com) and, on current'
  Write-Host '     builds, DevicePasswordLessBuildVersion set to 0 under'
  Write-Host '     HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
  Write-Host '     before the password field appears at all.'
}

if ($agentTasks.Count -gt 0 -and -not $signInMatters) {
  Write-Host ''
  Write-Host '   Neither route is needed as things stand: every registered task carries a'
  Write-Host '   stored credential and needs no desktop. Both are reported so you can see'
  Write-Host '   the state, and they matter again the moment a chart job is switched on.'
} elseif ($agentTasks.Count -gt 0 -and -not $desktopNeeded) {
  Write-Host ''
  Write-Host '   One of these routes is still load-bearing, even though no job needs a'
  Write-Host '   chart: the tasks are registered Interactive, so a sign-in is what starts'
  Write-Host '   them. Give register_desk_agent.ps1 the password and neither route'
  Write-Host '   matters any more.'
}

# The one finding worth shouting about. Plenty of guides tell you to set
# DefaultPassword here; it is stored in plaintext and any local user can read
# it. Sysinternals Autologon puts it in the LSA secret store instead, and
# leaves this value absent -- so its presence means someone did it by hand.
if ($null -ne $plain) {
  Write-Host ''
  Write-Host '   *** YOUR WINDOWS PASSWORD IS IN THE REGISTRY IN PLAINTEXT ***'
  Write-Host ('   ' + $winlogon)
  Write-Host '   value: DefaultPassword -- readable by any local user, and it syncs nowhere'
  Write-Host '   good. Delete it and set the sign-in up again with Sysinternals Autologon,'
  Write-Host '   which stores it as an LSA secret instead. In an ADMIN PowerShell'
  Write-Host '   (right-click > Run as administrator):'
  Write-Host ('     Remove-ItemProperty -Path "' + $winlogon + '" -Name DefaultPassword')
  Write-Host '   Deleting it stops the automatic sign-in until Autologon has been run,'
  Write-Host '   so do both, not just the delete.'
  $problems++
}

# 2. Is it awake at the scheduled minute?
Write-Host ''
Write-Host '2. Wake timers (the power plan, not the task)'
$acIdx = $null
$dcIdx = $null
try {
  $pc = & powercfg /q SCHEME_CURRENT SUB_SLEEP $wakeTimerGuid 2>&1 | Out-String
  $acMatch = [regex]::Match($pc, 'Current AC Power Setting Index:\s*(0x[0-9a-fA-F]+)')
  $dcMatch = [regex]::Match($pc, 'Current DC Power Setting Index:\s*(0x[0-9a-fA-F]+)')
  if ($acMatch.Success) { $acIdx = [Convert]::ToInt32($acMatch.Groups[1].Value, 16) }
  if ($dcMatch.Success) { $dcIdx = [Convert]::ToInt32($dcMatch.Groups[1].Value, 16) }
} catch { }

if ($null -eq $acIdx -and $null -eq $dcIdx) {
  # powercfg output is localized, so a parse failure here is not evidence that
  # wake timers are off. Say which of the two this is.
  Write-Host '   ?     could not read the setting (powercfg output not in the expected form).'
  Write-Host '         This is a parse failure, not a finding -- check by hand in'
  Write-Host '         Control Panel > Power Options > Change plan settings > Advanced.'
} else {
  # Name each index through a function rather than indexing the table inline.
  # Only one of the two regexes may have matched, and Hashtable.ContainsKey($null)
  # THROWS -- which, under $ErrorActionPreference = 'Stop', would end the whole
  # report over a cosmetic label on a line nobody was reading.
  $names = @{ 0 = 'Disabled'; 1 = 'Enabled'; 2 = 'Important Wake Timers Only' }
  $acName = '(not reported)'
  $dcName = '(not reported)'
  if ($null -ne $acIdx) {
    $acName = if ($names.ContainsKey($acIdx)) { $names[$acIdx] } else { 'unknown (' + $acIdx + ')' }
  }
  if ($null -ne $dcIdx) {
    $dcName = if ($names.ContainsKey($dcIdx)) { $names[$dcIdx] } else { 'unknown (' + $dcIdx + ')' }
  }
  Write-Host ('   plugged in: ' + $acName + '     on battery: ' + $dcName)
  if ($null -ne $acIdx -and $acIdx -eq 0) {
    Write-Host '         WakeToRun on the tasks is accepted, stored, and reads back True, and'
    Write-Host '         it still does nothing while this is Disabled. Turn it on in an'
    Write-Host '         ADMIN PowerShell (right-click > Run as administrator):'
    Write-Host ('           powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP ' + $wakeTimerGuid + ' 1')
    Write-Host '           powercfg /setactive SCHEME_CURRENT'
    $problems++
  }
}

# 3. What did the tasks actually get?
Write-Host ''
Write-Host '3. The desk agent tasks'
$tasks = @(Get-ScheduledTask -TaskName ($agentPrefix + '*') -ErrorAction SilentlyContinue)
if ($tasks.Count -eq 0) {
  Write-Host '   none registered. Run tools\register_desk_agent.ps1 first.'
  $problems++
} else {
  foreach ($t in $tasks) {
    $wake  = $t.Settings.WakeToRun
    $logon = $t.Principal.LogonType
    $suffix = $t.TaskName.Substring($agentPrefix.Length)
    $wantsDesktop = $desktopTasks -contains $suffix
    $desktopless  = @('S4U', 'Password') -contains "$logon"
    $need = if ($wantsDesktop) { 'needs a desktop' } else { 'no desktop needed' }
    Write-Host ('   ' + $t.TaskName.PadRight(24) + ' LogonType: ' + "$logon".PadRight(12) +
                ' WakeToRun: ' + "$wake".PadRight(6) + ' ' + $need)
    if (-not $wake) {
      Write-Host '         WakeToRun is off -- re-run tools\register_desk_agent.ps1.'
      $problems++
    }
    if ($wantsDesktop) {
      if ($desktopless) {
        # The trap this whole file exists for, caught after the fact.
        Write-Host '         WRONG: this job drives TradingView Desktop, and this logon'
        Write-Host '         type gets none. It will fire on time and then'
        Write-Host '         fail at the first chart call. Re-run register_desk_agent.ps1.'
        $problems++
      }
    } elseif (-not $desktopless) {
      # NOT counted as a problem. The job works exactly as it always did; it is
      # simply still tied to a signed-in machine for no reason left in the
      # code. Counting it would make a working desk report as broken, which is
      # the failure mode this rewrite exists to remove.
      Write-Host '         This job needs no desktop but is registered Interactive, so it'
      Write-Host '         still only runs while you are signed in. Re-run'
      Write-Host '         register_desk_agent.ps1 and give it the password to lift that.'
    }
  }
}

# 4. The lock task, which is optional and off by default.
Write-Host ''
Write-Host '4. Lock after logon (optional)'
$lock = Get-ScheduledTask -TaskName $lockTaskName -ErrorAction SilentlyContinue
if ($lock) {
  Write-Host ('   ON    ' + $lockTaskName + '  state: ' + $lock.State)
  Write-Host '         If journal chart captures come back blank, undo this first:'
  Write-Host '           .\tools\autologon.ps1 -DisableLock'
} else {
  Write-Host '   off   an unattended reboot leaves the desktop unlocked until your'
  Write-Host '         inactivity timer catches it. Turn it on with:'
  Write-Host '           .\tools\autologon.ps1 -EnableLock'
}

Write-Host ''
if ($problems -eq 0) {
  # NOT "all clear" when a desktop job exists. Nothing above read the ARSO
  # toggle, because it cannot be read from here, and a summary that rounds
  # "I could not check this" up to "this is fine" is the whole failure mode
  # this script was written against. Say what was verified, and name what
  # was not.
  #
  # With no desktop job registered there is nothing unverified left to warn
  # about: the ARSO toggle stops being load-bearing, so claiming it as an
  # open question would be its own kind of false report.
  if (-not $signInMatters -and $agentTasks.Count -gt 0) {
    # The only branch entitled to say the sign-in has stopped mattering: every
    # task actually carries a stored credential. Reached by supplying the
    # password, not by the jobs no longer needing a chart.
    Write-Host 'Nothing here is broken, and nothing needs signing in: every registered task'
    Write-Host 'carries a stored credential, so they run whether you are logged on or not. The'
    Write-Host 'machine will still wake itself for their triggers.'
    Write-Host ''
    Write-Host 'The ARSO toggle is not load-bearing while that holds, so it is not flagged above.'
    Write-Host 'Proof is the next 07:00 run appearing in the log after an overnight reboot.'
  } elseif (-not $desktopNeeded -and $agentTasks.Count -gt 0) {
    # No chart job, but the tasks are Interactive -- so the sign-in is still
    # what starts them. Saying otherwise here is the bug this branch fixes.
    Write-Host 'Nothing here is broken, but the conversion has NOT been applied: no registered'
    Write-Host 'task needs a desktop, yet they are registered Interactive, so they still only'
    Write-Host 'run while you are signed in. Section 3 names them.'
    Write-Host ''
    Write-Host 'So signing in after a restart still matters, and the per-user ARSO toggle above'
    Write-Host 'was NOT verified -- it cannot be read from here. Either confirm it by eye, or'
    Write-Host 're-run register_desk_agent.ps1 and supply the password, which removes the'
    Write-Host 'dependency altogether.'
  } else {
    Write-Host 'Nothing here is broken: no blocking policy, the tasks that want a desktop will get'
    Write-Host 'one, and they will wake the machine for their trigger.'
    Write-Host ''
    Write-Host 'ONE thing above was not verified and cannot be from here: the per-user ARSO toggle'
    Write-Host 'in Settings. Confirm that by eye. The actual proof is the next 07:00 run appearing'
    Write-Host 'in the log after an overnight reboot.'
  }
} else {
  Write-Host ('' + $problems + ' thing(s) above will stop an unattended run. Each one names its fix.')
}
Write-Host ''
