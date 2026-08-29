<#
.SYNOPSIS
  Report whether this machine can run the desk agent with nobody signed in.

.DESCRIPTION
  The desk agent's scheduled tasks are registered with a LogonType of
  Interactive, and that is deliberate. Both enabled jobs drive TradingView
  Desktop -- premarket reads session levels off the chart, journal captures
  chart images -- and an Electron app needs a real desktop to render on.

  It is tempting to answer "it did not run because I was not signed in" by
  setting the tasks to run whether the user is logged on or not. That does not
  work here. S4U and Password logon types get a session with NO DESKTOP, so the
  tasks would fire on time and then fail at the first chart call. A job that
  does not run is at least honest about it; a job that runs against no desktop
  produces a gameplan built on whatever the failure left behind.

  The fix is to make the machine sign ITSELF in, so a desktop session exists
  for the tasks to use. That is three separate things, and all three have to be
  true before a 07:00 job on an unattended machine actually produces anything:

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

# 1. Does it sign itself in?
$auto   = Get-WinlogonValue 'AutoAdminLogon'
$user   = Get-WinlogonValue 'DefaultUserName'
$domain = Get-WinlogonValue 'DefaultDomainName'
$plain  = Get-WinlogonValue 'DefaultPassword'

Write-Host '1. Automatic sign-in'
if ("$auto" -eq '1') {
  Write-Host ('   ON    AutoAdminLogon=1, user: ' + $domain + '\' + $user)
} else {
  Write-Host ('   OFF   AutoAdminLogon=' + $(if ($null -eq $auto) { '(not set)' } else { $auto }))
  Write-Host '         Nothing signs this machine in, so a task needing a desktop cannot run'
  Write-Host '         after a reboot. Set it with Sysinternals Autologon (see the README).'
  $problems++
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
    Write-Host ('   ' + $t.TaskName.PadRight(24) + ' LogonType: ' + "$logon".PadRight(12) + ' WakeToRun: ' + $wake)
    if (-not $wake) {
      Write-Host '         WakeToRun is off -- re-run tools\register_desk_agent.ps1.'
      $problems++
    }
    if ("$logon" -ne 'Interactive') {
      Write-Host '         NOT Interactive. These jobs drive TradingView Desktop and need a'
      Write-Host '         desktop; S4U and Password do not get one. Re-run register_desk_agent.ps1.'
      $problems++
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
  Write-Host 'All clear: this machine can sign itself in, wake itself, and the tasks want a desktop.'
} else {
  Write-Host ('' + $problems + ' thing(s) above will stop an unattended run. Each one names its fix.')
}
Write-Host ''
