<#
.SYNOPSIS
  Run one desk-agent job headless, and make sure the run gets recorded even
  when the agent itself never starts.

.DESCRIPTION
  Invoked by the Windows scheduled tasks that tools/register_desk_agent.ps1
  creates. It resolves Claude Code, fixes the console encoding, hands the job
  to the agent, and tees the output to a log file.

  The part that matters is the fallback: if claude.exe cannot be found or exits
  non-zero, this script appends a "failed" run record itself. Without that, a
  broken launcher produces no record at all -- and a gap in the log is
  indistinguishable from a scheduler that never fired, which is the one thing
  the whole design is built to tell apart.

.PARAMETER Job
  Job name, matching a file under tools/desk_agent/jobs/.

.PARAMETER RepoRoot
  Repository root. Defaults to two levels above this script, so it follows the
  checkout it was run from rather than assuming which one that is.

.PARAMETER AddDir
  Extra directories to hand the agent, on top of the ones its job gets by
  default. Repeatable. A path that does not exist is logged and dropped rather
  than passed on, so a wrong path cannot take the whole run down with it.

.EXAMPLE
  .\tools\desk_agent\run_job.ps1 -Job premarket

.EXAMPLE
  .\tools\desk_agent\run_job.ps1 -Job journal -AddDir 'D:\somewhere\else'
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('premarket', 'alerts', 'journal', 'pine_loop', 'review')]
  [string] $Job,

  [string] $RepoRoot,

  [string[]] $AddDir = @()
)

$ErrorActionPreference = 'Stop'

# Where the repository is. Guessing from $PSScriptRoot only worked while this
# script lived inside the repo; installed to %LOCALAPPDATA% it walks up into
# AppData and finds nothing, which then breaks the log path, the git branch
# read, and the python import all at once. So: the explicit parameter wins, then
# a pointer file written at install time, and only then the old guess -- which
# is still correct when running this straight out of a checkout.
if (-not $RepoRoot) {
  $pointer = Join-Path $PSScriptRoot 'repo_root.txt'
  if (Test-Path -LiteralPath $pointer) {
    $RepoRoot = (Get-Content -LiteralPath $pointer -Raw).Trim()
  } else {
    $RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
  }
}
if (-not (Test-Path -LiteralPath $RepoRoot)) { throw "Repo root not found: $RepoRoot" }
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
  throw "Not a git checkout: $RepoRoot -- pass -RepoRoot, or re-run register_desk_agent.ps1 to write the pointer file."
}

# Logs live OUTSIDE the repo on purpose. A checkout sitting on the wrong branch
# has no tools/desk_agent/ at all -- which is precisely the case where the
# evidence matters most, and precisely when a repo-relative log path cannot be
# written. Three silent failures in this system have now come from evidence
# having nowhere to go.
$logDir = Join-Path $env:LOCALAPPDATA 'pwb-desk-agent\logs'
if (-not (Test-Path -LiteralPath $logDir)) {
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$stamp   = (Get-Date).ToString('yyyy-MM-dd_HHmm')
$logFile = Join-Path $logDir ($Job + '-' + $stamp + '.log')

function Write-Log([string] $text) {
  Write-Host $text
  [IO.File]::AppendAllText($logFile, $text + "`r`n", (New-Object Text.UTF8Encoding $false))
}

# Reading a native command's stdout mangles UTF-8 unless both of these are set:
# the console default is the OEM codepage, which turns the agent's output into
# mojibake that then gets written to the log faithfully.
try {
  [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
  $OutputEncoding           = New-Object Text.UTF8Encoding $false
} catch { }

# -- resolve python, for the fallback record -----------------------------------
$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python' }

function Record-Failure([string] $blocker, [string] $summary) {
  # Best effort. If even this cannot run there is nothing further to try, and
  # the missing record is itself the signal when the next review reads the log.
  try {
    Push-Location $RepoRoot
    & $python -m tools.desk_agent.runlog append --job $Job --outcome failed `
      --summary $summary --blocker $blocker | Out-Null
  } catch {
    Write-Log ("could not write the failure record: " + $_.Exception.Message)
  } finally {
    Pop-Location
  }
}

# -- resolve Claude Code -------------------------------------------------------
# claude.exe is the native install; claude.cmd is the old npm global. Never
# claude.ps1 -- execution policy blocks it.
$claude = $null
foreach ($candidate in @(
    (Join-Path $HOME '.local\bin\claude.exe'),
    (Join-Path $HOME '.local\bin\claude.cmd')
  )) {
  if (Test-Path -LiteralPath $candidate) { $claude = $candidate; break }
}
if (-not $claude) {
  $found = Get-Command claude.exe, claude.cmd -ErrorAction SilentlyContinue |
           Select-Object -First 1
  if ($found) { $claude = $found.Source }
}

# ToUniversalTime matters: ToString('u') appends a 'Z' but does NOT convert, so
# a local timestamp goes into the log labelled as UTC and disagrees with
# runs.jsonl, which is real UTC.
Write-Log ("[" + (Get-Date).ToUniversalTime().ToString('u') + "] job=" + $Job + " repo=" + $RepoRoot)

# The desk agent may simply not be on the branch this checkout has open. Other
# sessions switch branches in this working tree constantly, and until the agent
# is merged to main it exists on exactly one of them. Say so rather than dying
# with no explanation: a task that fails with an empty log is indistinguishable
# from a task that never fired, and telling those apart is the whole point.
$playbook = Join-Path $RepoRoot 'tools\desk_agent\playbook.md'
$jobFile  = Join-Path $RepoRoot ('tools\desk_agent\jobs\' + $Job + '.md')
try { $branch = (& git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null) } catch { $branch = '(unknown)' }
Write-Log ("branch: " + $branch)

if (-not (Test-Path -LiteralPath $playbook) -or -not (Test-Path -LiteralPath $jobFile)) {
  Write-Log 'The desk agent is not present in this checkout.'
  Write-Log ("  missing: " + $(if (Test-Path -LiteralPath $playbook) { $jobFile } else { $playbook }))
  Write-Log ("  branch " + $branch + " does not carry tools/desk_agent/.")
  Write-Log '  Check out a branch that does, or merge the desk agent to main.'
  Record-Failure ('desk agent not present on branch ' + $branch) `
                 ('Nothing ran: the checkout is on branch ' + $branch + ', which does not carry tools/desk_agent/. No chart was read and no gameplan written.')
  exit 2
}

if (-not $claude) {
  Write-Log 'Claude Code not found (looked for claude.exe then claude.cmd).'
  Record-Failure 'claude executable not found on PATH' `
                 'launcher could not resolve Claude Code, job never started'
  exit 1
}
Write-Log ("claude: " + $claude)

# Whether TradingView was already running decides whether we are allowed to
# close it afterwards. If the owner had it open, killing it would destroy their
# session; if the agent started it, leaving it open would leave an
# unauthenticated debug port on the machine -- which is the risk
# docs/tradingview-agent-security.md exists for.
$tvBefore = @(Get-Process -Name 'TradingView*' -ErrorAction SilentlyContinue)
$tvWasAlreadyRunning = $tvBefore.Count -gt 0
Write-Log ("tradingview already running: " + $tvWasAlreadyRunning)

# -- directories the agent may read beyond the repo -----------------------------
# A headless session is confined to its working directory. The journal job's
# entire subject -- trade-journal.html -- lives outside it, so without this that
# job could do nothing but log the same blocker, which it did on five
# consecutive weekdays while the paper record moved twice underneath it. The
# agent cannot fix this for itself: widening its own access is precisely what
# the guardrail forbids, so it kept filing the request instead. This is the
# answer to that request.
#
# Scoped per job on purpose. Handing every job the journal folder would put a
# personal document inside the blast radius of an hourly unattended run with no
# business reading it; the other jobs keep the narrower boundary they have now.
$jobDirs = @{
  journal = @(Join-Path $HOME 'OneDrive\trade-journal')
}

$claudeArgs = @('-p')
foreach ($dir in (@($jobDirs[$Job]) + $AddDir)) {
  if ([string]::IsNullOrWhiteSpace($dir)) { continue }
  # Drop a path that is not there rather than passing it on. A bad --add-dir
  # takes down a run that had nothing else wrong with it, and the resulting
  # failure names the flag rather than the job -- the same class of misleading
  # evidence this launcher exists to prevent. Logged either way, so a journal
  # run that finds nothing to read still says why in its own log.
  if (Test-Path -LiteralPath $dir) {
    $claudeArgs += @('--add-dir', $dir)
    Write-Log ("added dir: " + $dir)
  } else {
    Write-Log ("NOT added, path does not exist: " + $dir)
  }
}

# -- run -----------------------------------------------------------------------
$prompt = @(
  'You are the desk agent running unattended.',
  ('Read tools/desk_agent/playbook.md, then tools/desk_agent/jobs/' + $Job + '.md,'),
  'and carry out that job now.',
  'If you need a chart and the CDP port is not up, call tv_launch: this launcher',
  'closes TradingView after you exit, so launching is no longer a one-way door.',
  'Append exactly one run record when you finish, as the playbook describes -',
  'including if you did nothing.'
) -join ' '

Push-Location $RepoRoot
try {
  $output = $prompt | & $claude @claudeArgs 2>&1 | Out-String
  $code = $LASTEXITCODE
} catch {
  $output = $_.Exception.Message
  $code = 1
} finally {
  Pop-Location

  # Close the debug port the agent opened. This lives here rather than in the
  # playbook on purpose: the agent correctly refused to tv_launch while it had
  # no permitted way to quit, because launching would have been a one-way door.
  # Doing it in the launcher makes shutdown mechanical instead of a request the
  # agent has to remember, and it still runs when the agent crashes.
  if (-not $tvWasAlreadyRunning) {
    $tvAfter = @(Get-Process -Name 'TradingView*' -ErrorAction SilentlyContinue)
    if ($tvAfter.Count -eq 0) {
      Write-Log 'no TradingView process to close'
    } else {
      foreach ($proc in $tvAfter) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch { }
      }
      # Verify by re-querying, not by whether Stop-Process threw. Electron runs
      # several processes under one name, and killing the main one takes its
      # children with it -- so a "no such process" error here is usually success
      # arriving early. Warning on the throw would fire on every normal
      # shutdown, and a warning that cries wolf is how a log stops being read.
      Start-Sleep -Milliseconds 500
      $stillUp = @(Get-Process -Name 'TradingView*' -ErrorAction SilentlyContinue)
      if ($stillUp.Count -eq 0) {
        Write-Log ("closed TradingView (" + $tvAfter.Count + " process(es))")
      } else {
        Write-Log ("WARNING: " + $stillUp.Count + " TradingView process(es) survived Stop-Process.")
        Write-Log 'The CDP debug port may still be open. Close TradingView by hand.'
      }
    }
  } else {
    Write-Log 'left TradingView running: it was already open before this run'
  }
}

Write-Log $output

if ($code -ne 0) {
  Write-Log ("claude exited with code " + $code)
  Record-Failure ('claude exited non-zero: ' + $code) `
                 ('agent run failed with exit code ' + $code)
  exit $code
}

Write-Log 'done'
exit 0
