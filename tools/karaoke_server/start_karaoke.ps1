<#
.SYNOPSIS
  Start karaoke night. Double-click the Desktop icon; the big screen opens by
  itself and the join address is printed large enough to read to a room.

.DESCRIPTION
  This is the whole no-brainer path. It exists because on 2026-09-02 starting
  karaoke by hand went wrong four separate ways in one sitting, and every one
  of them looked like the product was broken rather than the command:

    1. a stale checkout   -> the running code was not the code that was fixed
    2. a VPN address      -> the QR published 10.5.0.2 and no phone resolved it
    3. Windows Firewall   -> phones reached nothing, with no message anywhere
    4. an empty Read-Host -> a prompt returned '' straight into a --host flag

  So: nothing here is typed. The script finds Python, refuses to start on a
  busy port with a sentence rather than a traceback, asks the SERVER which
  address phones can reach (never a second copy of that ranking, which is how
  failure 2 comes back), checks the firewall rule and prints the exact fix
  rather than elevating behind anyone's back, waits for the port to answer,
  opens the big screen, and stops the server when the window closes.

  Written for Windows PowerShell 5.1: ASCII bytes only, no bash-style command
  chaining, no '~' for home, no here-strings. One non-ASCII byte in a BOM-less
  .ps1 makes 5.1 fail to parse the entire file and print nothing at all.

.PARAMETER Port
  TCP port for the queue server. Default 8772.

.PARAMETER Address
  Pin the address the QR publishes, when the server's guess is wrong. NOT
  named -Host: $Host is an automatic variable in PowerShell and a parameter
  by that name collides with it.

.PARAMETER NoBrowser
  Do not open the big screen here. For running the server on a machine whose
  screen nobody is looking at.

.EXAMPLE
  .\tools\karaoke_server\start_karaoke.ps1

.EXAMPLE
  .\tools\karaoke_server\start_karaoke.ps1 -Address 192.168.1.50
#>
[CmdletBinding()]
param(
  [int] $Port = 8772,

  [string] $Address,

  [switch] $NoBrowser
)

$ErrorActionPreference = 'Stop'

# Must match FIREWALL_RULE_NAME in tools/karaoke_server/queue_server.py.
# tests/test_karaoke_launcher.py fails if the two ever drift, because a check
# looking for one name and a fix creating another reads as "already allowed"
# forever.
$RuleName = 'Karaoke Queue'


# ---------------------------------------------------------------- plumbing --

function Write-Blank {
  Write-Host ''
}

function Stop-Here([string] $text) {
  # Never a stack trace, and never a message that vanishes. Launched from the
  # Desktop shortcut this console closes the instant the script ends, so a
  # failure that does not pause is a failure nobody ever reads.
  Write-Blank
  Write-Host $text -ForegroundColor Yellow
  Write-Blank
  Write-Host 'Press Enter to close this window.'
  try { [void](Read-Host) } catch { }
  exit 1
}

function Test-PythonCandidate([string] $exe, [string[]] $pre) {
  # Two things must be true: it runs, and it is really Python 3.10+.
  #
  # A Windows box with no Python still has a 'python.exe' on PATH -- the
  # Microsoft Store stub, which prints nothing, exits 9009, and opens the
  # Store. Checking only "did the command resolve" hands that stub the night.
  #
  # ErrorActionPreference is dropped to Continue around the call because on
  # 5.1 a native command writing to stderr under '2>&1' with Stop in force
  # raises NativeCommandError -- the check would fail on a working Python.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $text = ''
  try {
    $raw = & $exe @pre '--version' 2>&1
    $text = ($raw | Out-String).Trim()
  } catch {
    $text = ''
  }
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($code -ne 0) { return $false }
  # [regex]::Match rather than -match plus $Matches: the automatic $Matches is
  # assigned by the engine and not by this file, which reads as a dangling
  # lookup to anyone -- and to tests/test_desk_agent_launcher.py -- checking
  # that nothing here indexes a table it never built.
  $found = [regex]::Match($text, 'Python 3\.(\d+)')
  if (-not $found.Success) { return $false }
  return ([int] $found.Groups[1].Value -ge 10)
}

function Test-PortBusy([int] $p) {
  # Ask the operating system for the port rather than parsing a process list:
  # if this listener cannot have it, neither can the server.
  $listener = $null
  try {
    $listener = New-Object Net.Sockets.TcpListener -ArgumentList @([Net.IPAddress]::Any, $p)
    $listener.Start()
    return $false
  } catch {
    return $true
  } finally {
    if ($listener) { try { $listener.Stop() } catch { } }
  }
}

function Test-PortAnswers([string] $addr, [int] $p) {
  $client = New-Object Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect($addr, $p, $null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne(500)) { return $false }
    $client.EndConnect($async)
    return $true
  } catch {
    return $false
  } finally {
    try { $client.Close() } catch { }
  }
}

function Test-Elevated {
  try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal -ArgumentList $id
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch {
    return $false
  }
}

function Get-FirewallState([int] $p) {
  # 'ok' | 'missing' | 'unknown'. The third value is the point: a check that
  # cannot run must not answer 'missing' and must not answer 'ok'. Suppressed
  # errors reading as "checked, found nothing" have cost this project real
  # time before, so the caller is told which of the three it got.
  if (-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
    return 'unknown'
  }
  $rules = @()
  try {
    $rules = @(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)
  } catch {
    return 'unknown'
  }
  if ($rules.Count -eq 0) { return 'missing' }
  foreach ($rule in $rules) {
    if ("$($rule.Enabled)" -ne 'True') { continue }
    if ("$($rule.Direction)" -ne 'Inbound') { continue }
    if ("$($rule.Action)" -ne 'Allow') { continue }
    $ports = @()
    try {
      $ports = @($rule | Get-NetFirewallPortFilter | Select-Object -ExpandProperty LocalPort)
    } catch {
      $ports = @()
    }
    foreach ($lp in $ports) {
      # A rule for the wrong port is not a rule. The port moved once already.
      if ("$lp" -eq 'Any' -or "$lp" -eq "$p") { return 'ok' }
    }
  }
  return 'missing'
}


# ------------------------------------------------------------- the checkout --

# Derived from where this file sits, never from a hard-coded user path: there
# are two pwb-toolbox checkouts on this machine and the folder may also have
# been copied somewhere else entirely. Whichever one the icon points at is the
# one that runs.
$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$serverModule = Join-Path $RepoRoot 'tools\karaoke_server\queue_server.py'
if (-not (Test-Path -LiteralPath $serverModule)) {
  Stop-Here ("Karaoke could not find its own program files.`r`n" +
    "Expected: $serverModule`r`n" +
    'Move this folder back into the pwb-toolbox checkout, or re-run install_shortcut.ps1 from the right one.')
}


# ------------------------------------------------------------------ python --

$pythonExe = $null
$pythonPre = @()

$candidates = @()
$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
  $candidates += ,@{ Exe = $venvPython; Pre = @() }
}
$candidates += ,@{ Exe = 'python'; Pre = @() }
$candidates += ,@{ Exe = 'py'; Pre = @('-3') }
if ($env:LOCALAPPDATA) {
  $known = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
  if (Test-Path -LiteralPath $known) {
    $candidates += ,@{ Exe = $known; Pre = @() }
  }
}

foreach ($c in $candidates) {
  if (Test-PythonCandidate $c.Exe $c.Pre) {
    $pythonExe = $c.Exe
    $pythonPre = $c.Pre
    break
  }
}

if (-not $pythonExe) {
  Stop-Here ("Karaoke needs Python and this computer does not have it yet.`r`n`r`n" +
    "Install it from https://www.python.org/downloads/windows/`r`n" +
    "Tick 'Add python.exe to PATH' on the first screen of the installer.`r`n" +
    'Then double-click the Karaoke icon again.')
}


# -------------------------------------------------------------- a busy port --

if (Test-PortBusy $Port) {
  Stop-Here ("Karaoke is already running -- close the other karaoke window first.`r`n`r`n" +
    "(Something on this computer is already using port $Port. If you are sure " +
    'karaoke is not open, restart the computer.)')
}


# ---------------------------------------------------------------- firewall --

$fixCommand = "New-NetFirewallRule -DisplayName '$RuleName' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any"
$firewall = Get-FirewallState $Port

if ($firewall -eq 'missing') {
  Write-Blank
  Write-Host 'Phones will not be able to connect: Windows Firewall is not letting them in yet.' -ForegroundColor Yellow
  if (Test-Elevated) {
    Write-Host 'This window is running as administrator, so it can be fixed right now.'
    $answer = ''
    try { $answer = Read-Host 'Add the firewall rule now? [Y/n]' } catch { $answer = '' }
    # An empty answer means yes, and it is never passed on as an argument.
    # A Read-Host value going straight into a command flag is exactly how the
    # server was started with an empty --host on 2026-09-02.
    if ($answer -eq '' -or $answer -match '^(y|yes)$') {
      try {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
        Write-Host "Added the firewall rule '$RuleName'. Phones can reach this computer now." -ForegroundColor Green
        $firewall = 'ok'
      } catch {
        Write-Host 'Could not add the rule. Karaoke will still start.' -ForegroundColor Yellow
      }
    }
  }
  if ($firewall -ne 'ok') {
    # No silent elevation. A double-clicked icon that pops a UAC prompt and
    # rewrites the host firewall is not a thing to ship; the command is
    # printed and the decision stays with whoever opened the window.
    Write-Host 'Karaoke will still start, and the big screen here will work.'
    Write-Host 'To let phones join, open PowerShell AS ADMINISTRATOR once and paste this line:'
    Write-Blank
    Write-Host "    $fixCommand" -ForegroundColor Cyan
    Write-Blank
    Write-Host '(Right-click the Start button, choose "Windows PowerShell (Admin)" or "Terminal (Admin)".)'
  }
  Write-Blank
} elseif ($firewall -eq 'unknown') {
  Write-Host 'Could not check the Windows Firewall on this computer. If phones cannot join, paste this into an administrator PowerShell window:'
  Write-Host "    $fixCommand" -ForegroundColor Cyan
  Write-Blank
}


# ------------------------------------------- the address phones can reach --

# Asked of the server, never worked out again here. queue_server ranks every
# address this machine answers to, putting the range a house or a venue hands
# out above the one VPN clients and container bridges help themselves to --
# because a VPN tunnel is exactly what got published into the QR on
# 2026-09-02. A second copy of that ranking in PowerShell is a second thing
# to get wrong, on the path nothing tests.
$baseArgs = @('-m', 'tools.karaoke_server.queue_server', '--port', "$Port")
if ($Address) { $baseArgs += @('--host', $Address) }

$reachable = ''
$prevPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
Push-Location -LiteralPath $RepoRoot
try {
  # From the repo root, or 'python -m tools.karaoke_server...' finds nothing.
  # The Desktop shortcut sets this too, but the script must not depend on it:
  # run from a PowerShell window the working directory is wherever they were.
  $addrArgs = $pythonPre + $baseArgs + @('--print-address')
  $printed = & $pythonExe @addrArgs 2>$null
  if ($printed) { $reachable = ("$($printed | Select-Object -First 1)").Trim() }
} catch {
  $reachable = ''
} finally {
  Pop-Location
  $ErrorActionPreference = $prevPref
}
if ([string]::IsNullOrWhiteSpace($reachable)) {
  # Never build a URL out of an empty value. 'http://:8772/' is the shape of
  # the bug that started this script.
  $reachable = 'localhost'
}

# ${reachable}: and not $reachable: -- PowerShell reads 'name:' as a drive
# qualifier on a variable, so the unbraced form silently produces nonsense.
$joinUrl   = "http://${reachable}:${Port}/"
$screenUrl = "http://${reachable}:${Port}/screen"


# -------------------------------------------------------------- singer memory --

# Outside the checkout on purpose: memory that lands in the repo is memory
# that shows up in 'git status' and eventually in a commit, and it follows the
# machine rather than whichever clone the icon happens to point at.
$memoryHome = $env:LOCALAPPDATA
if (-not $memoryHome) { $memoryHome = $env:USERPROFILE }
$memoryDir = Join-Path $memoryHome 'karaoke'
if (-not (Test-Path -LiteralPath $memoryDir)) {
  New-Item -ItemType Directory -Path $memoryDir -Force | Out-Null
}
$profilesPath = Join-Path $memoryDir 'karaoke-profiles.json'


# ------------------------------------------------------------------ launch --

$outLog = Join-Path $env:TEMP 'karaoke-server.out.log'
$errLog = Join-Path $env:TEMP 'karaoke-server.err.log'
$env:KARAOKE_QUIET = '1'

$serverArgs = $pythonPre + $baseArgs + @('--profiles', $profilesPath)

# One pre-quoted string rather than an array. Start-Process on 5.1 joins an
# -ArgumentList array with spaces and its quoting of elements that contain
# them is not something to rely on: a user folder with a space in it would
# split one argument into two and the server would start with no --profiles
# at all. Quoting here makes the command line exactly what is intended.
$argLine = (($serverArgs | ForEach-Object {
  if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
}) -join ' ')

Write-Host 'Starting karaoke...'

$proc = $null
try {
  # -NoNewWindow keeps the server attached to THIS console, which is what
  # makes closing the window stop it: Windows sends the close event to every
  # process on the console. Output goes to files so this window stays quiet
  # enough to read the join address off.
  $proc = Start-Process -FilePath $pythonExe -ArgumentList $argLine `
    -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
} catch {
  Stop-Here ("Karaoke could not start Python.`r`n" + $_.Exception.Message)
}

try {
  $probe = if ($Address) { $Address } else { '127.0.0.1' }
  $deadline = (Get-Date).AddSeconds(20)
  $up = $false
  while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) { break }
    if (Test-PortAnswers $probe $Port) { $up = $true; break }
    Start-Sleep -Milliseconds 300
  }

  if (-not $up) {
    $detail = ''
    foreach ($log in @($errLog, $outLog)) {
      if (Test-Path -LiteralPath $log) {
        $tail = (Get-Content -LiteralPath $log -Tail 8 -ErrorAction SilentlyContinue) -join "`r`n"
        if ($tail) { $detail = $detail + "`r`n" + $tail }
      }
    }
    if (-not $proc.HasExited) {
      try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    Stop-Here ("Karaoke started but never answered on port $Port." + $detail)
  }

  Write-Blank
  Write-Host 'Karaoke is running. Read this out to the room:' -ForegroundColor Green
  $line = '   ' + $joinUrl + '   '
  $bar = '=' * $line.Length
  Write-Host $bar -ForegroundColor Green
  Write-Host $line -ForegroundColor Green
  Write-Host $bar -ForegroundColor Green
  Write-Blank
  Write-Host 'Or just scan the QR code on the big screen.'
  if ($reachable -eq 'localhost') {
    Write-Host 'WARNING: no network address was found, so only this computer can join.' -ForegroundColor Yellow
  }
  Write-Blank

  if (-not $NoBrowser) {
    try {
      Start-Process $screenUrl | Out-Null
    } catch {
      Write-Host "Could not open the big screen. Open this in a browser: $screenUrl" -ForegroundColor Yellow
    }
  }

  Write-Host 'Leave this window open. Close it, or press Ctrl+C, to end karaoke.'
  while (-not $proc.HasExited) {
    Start-Sleep -Milliseconds 500
  }
} finally {
  # Nothing is allowed to survive this window. An orphaned python holding the
  # port is what makes the NEXT double-click say "already running" when
  # nothing is.
  if ($proc -and -not $proc.HasExited) {
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
  }
}

Write-Host 'Karaoke stopped.'
exit 0
