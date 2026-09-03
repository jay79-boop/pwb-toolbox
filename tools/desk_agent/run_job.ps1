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

  After the agent exits, however it exits, the launcher commits any record left
  uncommitted and pushes the branch to the fork ('jay'), then verifies the push
  with 'runlog unpushed'. A record that is committed and never pushed is
  invisible to every cloud session, which is the one reader the log exists for.

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

# -- resolve python, and put the repo venv ahead of the child's PATH -----------
# Two uses, and the second is why this block is no longer only about the
# fallback record. $python runs runlog from THIS script. The PATH line is for
# the agent: claude and everything it spawns inherit this process's environment,
# and without the venv on PATH a bare `python` in an agent tool call resolves to
# the system install -- which is not the interpreter this repo's requirements
# are installed into. On 2026-09-01 that surfaced as ModuleNotFoundError for
# matplotlib inside desk_levels' chart path, on a machine whose .venv had it.
# Scoped to this process: nothing here edits a persisted PATH.
$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python' }
$venvScripts = Join-Path $RepoRoot '.venv\Scripts'
if (Test-Path -LiteralPath $venvScripts) {
  $env:PATH = $venvScripts + ';' + $env:PATH
}

function Get-OutputTail([string] $text, [int] $max = 600) {
  # What reaches the record has to be bounded, and it has to be one line.
  #
  # runs.jsonl is COMMITTED -- that is the whole reason raw stdout under logs/
  # is not -- so this takes the TAIL rather than the buffer. A crash says why
  # at the end, while a full transcript in a tracked file is both noise and a
  # place for chart detail to end up in a public fork.
  #
  # Double quotes become single because PowerShell 5.1 mangles them passing a
  # string to a native command, and a mangled argument loses the entire record
  # rather than one character -- which would be this fix causing the exact
  # silence it exists to remove.
  if ([string]::IsNullOrWhiteSpace($text)) { return '' }
  $flat = ($text -replace '\s+', ' ').Replace('"', "'").Trim()
  if ($flat.Length -le $max) { return $flat }
  return '...' + $flat.Substring($flat.Length - $max)
}

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

# -- publish the record --------------------------------------------------------
# The log is committed so that a cloud session can read it, and a cloud session
# reads GitHub, not this disk. A commit that never leaves the machine is exactly
# as invisible there as a record that was never written. From 2026-08-31 to
# 09-01 six commits -- four run records and two notes -- sat on the OneDrive
# main while GitHub's copy of runs.jsonl stopped at 08-28: the playbook said
# "commit" and nothing said "push", and nothing noticed for four days.
#
# This is the push. It lives in the launcher and not in the playbook on
# purpose: it runs after the agent exits, whether the agent finished, crashed,
# or never started, so the failure record Record-Failure writes reaches GitHub
# too. An instruction to the agent covers only the runs the agent completes.
#
# The remote is 'jay' by name. 'origin' is upstream in the OneDrive checkout
# and the fork in the other, so a bare origin push is wrong in one of them and
# fails by succeeding. docs/local-checkout.md has the table.
$remote = 'jay'

function Publish-RunLog([string] $onBranch) {
  # Native git writes progress to stderr, and under Stop that becomes a
  # terminating error the moment stderr is redirected. Nothing in here may
  # take the run down: the record is already written, and this only decides
  # whether GitHub sees it.
  $ErrorActionPreference = 'Continue'
  $paths = @('tools/desk_agent/runs.jsonl', 'tools/desk_agent/out')

  Push-Location $RepoRoot
  try {
    # 1. Commit anything the run left behind in the log or the output
    #    directory. The agent commits its own work; this catches the record
    #    the launcher wrote for a run that crashed or never started. The paths
    #    are named and nothing else is staged -- never 'git add -A', which is
    #    how commit dd6d1d6 once committed the deletion of the log.
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'tools\desk_agent\runs.jsonl'))) {
      # 'git add' of a tracked file that is gone stages its deletion, and that
      # is the dd6d1d6 accident with a scheduler behind it. Refuse, loudly.
      Write-Log 'NOT COMMITTED: runs.jsonl is missing from the checkout. Refusing to commit its deletion.'
      Write-Log '               Restore it: git checkout HEAD -- tools/desk_agent/runs.jsonl'
      return
    }
    $dirty = (& git status --porcelain -- $paths 2>&1 | Out-String).Trim()
    if ($dirty) {
      & git add -- $paths 2>&1 | Out-Null
      $message = 'desk agent: ' + $Job + ' run record, committed by the launcher'
      & git commit -q -m $message -- $paths 2>&1 | ForEach-Object { Write-Log ('  ' + $_) }
      if ($LASTEXITCODE -ne 0) {
        Write-Log 'WARNING: commit failed. The record is in the working tree only.'
      } else {
        Write-Log 'committed the run record (the agent had not)'
      }
    }

    if (-not $onBranch -or $onBranch -eq 'HEAD' -or $onBranch -eq '(unknown)') {
      Write-Log 'NOT PUSHED: detached HEAD or unknown branch. The record is committed here only.'
      return
    }
    $url = (& git remote get-url $remote 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $url) {
      Write-Log ("NOT PUSHED: no remote named '" + $remote + "' in this checkout. The record is committed here only.")
      return
    }

    # 2. On main, take what GitHub has first. main moves there every time a
    #    pull request merges, and a push from behind is refused as
    #    non-fast-forward -- so without this step the push would fail on most
    #    days while looking like it ran. It is the line the owner runs by hand
    #    (docs/local-checkout.md). A conflict is aborted, never left half-merged
    #    for the morning. gc.auto is forced off because a fetch that stops to
    #    ask about a OneDrive-locked object directory is an unanswerable hang
    #    from a task with no stdin.
    if ($onBranch -eq 'main') {
      & git -c gc.auto=0 fetch $remote main 2>&1 | Out-Null
      if ($LASTEXITCODE -ne 0) {
        Write-Log ('WARNING: git fetch ' + $remote + ' main failed; pushing without it')
      } else {
        & git -c gc.auto=0 merge --no-edit ($remote + '/main') 2>&1 | ForEach-Object { Write-Log ('  ' + $_) }
        if ($LASTEXITCODE -ne 0) {
          & git merge --abort 2>&1 | Out-Null
          Write-Log ('NOT PUSHED: merging ' + $remote + '/main conflicted. Merge aborted, tree left as it was.')
          Write-Log ('           By hand: git fetch ' + $remote + ' main; git merge --no-edit ' + $remote + '/main; git push ' + $remote + ' main')
          return
        }
      }
    }

    # 3. Push, then verify by asking git rather than trusting the exit code.
    #    The verification is the same command a cloud session or the next run
    #    can use, and it exits 1 when this machine knows runs GitHub does not.
    & git push $remote $onBranch 2>&1 | ForEach-Object { Write-Log ('  ' + $_) }
    if ($LASTEXITCODE -ne 0) {
      Write-Log ('WARNING: git push ' + $remote + ' ' + $onBranch + ' failed')
    }
    & $python -m tools.desk_agent.runlog unpushed --remote $remote --branch $onBranch 2>&1 |
      ForEach-Object { Write-Log ('  ' + $_) }
    if ($LASTEXITCODE -eq 0) {
      Write-Log ('pushed: ' + $remote + '/' + $onBranch + ' carries the run log')
    } else {
      Write-Log ('NOT PUSHED: ' + $remote + '/' + $onBranch + ' does not carry every record. See above.')
    }
  } catch {
    Write-Log ('could not publish the run log: ' + $_.Exception.Message)
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
  Publish-RunLog $branch
  exit 1
}
Write-Log ("claude: " + $claude)

# Which jobs still drive TradingView Desktop.
#
# Since 2026-08-29 `premarket` and `journal` do not: they read session levels,
# the prior day's range and their fair value gaps from bar data through
# tools/desk_levels.py, and render their own chart images headless. That is
# what lets their scheduled tasks take a logon type with no desktop -- see
# register_desk_agent.ps1, whose $jobs table carries the same fact as
# NeedsDesktop and is checked against this list by
# tests/test_desk_agent_launcher.py.
#
# This is a LIST rather than a deletion of the block below. `alerts` is off but
# not retired, and `pine_loop` runs on demand; both still want the chart, and
# both still want it closed behind them.
$desktopJobs = @('alerts', 'pine_loop')
$needsDesktop = $desktopJobs -contains $Job
Write-Log ("needs a desktop: " + $needsDesktop)

# Whether TradingView was already running decides whether we are allowed to
# close it afterwards. If the owner had it open, killing it would destroy their
# session; if the agent started it, leaving it open would leave an
# unauthenticated debug port on the machine -- which is the risk
# docs/tradingview-agent-security.md exists for.
#
# Asked even for a desktop-free job, and deliberately. The question this
# answers is "did WE open it", and the honest answer for a job that never
# launches anything is "no, and there is nothing here to close" -- which is
# what the shutdown block below then reports. Skipping the query would make a
# desktop-free run silent about a TradingView the owner had open, and silence
# is what this launcher exists to remove.
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

# -- what this job is allowed to run -------------------------------------------
# The grant lives here rather than in .claude/settings.json, for two reasons and
# both are deliberate.
#
# That file's allowlist is guarded: a session cannot edit it, by design. An
# agent able to widen its own permissions does not have permissions, and the
# desk agent's own guardrail says the same thing in its own words -- three run
# records in a row identified this exact fix and correctly refused to apply it.
#
# And a grant there would reach every session in this checkout, interactive ones
# included, when what needs it is this unattended run and nothing else. Narrower
# is the point, not a consolation.
#
# Why it is needed: premarket and journal both stopped at requires-approval on
# their first step, ten runs between them, because python tools/desk_levels.py
# is not permitted and a headless run has nobody to answer a prompt. Granted in
# the path form because that is the form the job files invoke --
# jobs/premarket.md lines 18-19 and jobs/journal.md line 28 -- and in both
# interpreter spellings, matching how the runlog grants are written.
#
# --allowedTools ADDS to what settings.json already permits rather than
# replacing it, so the runlog grants this job needs to write its own record are
# untouched. tests/test_desk_agent_launcher.py checks every command the job
# files invoke is covered by one of the two lists, so a new step in a job file
# cannot quietly reintroduce the fortnight of denials this ended.
$jobTools = @(
  'Bash(python tools/desk_levels.py:*)',
  'Bash(python3 tools/desk_levels.py:*)',
  'Bash(python tools/backtest_lab.py:*)',
  'Bash(python3 tools/backtest_lab.py:*)'
)
$claudeArgs += @('--allowedTools') + $jobTools
Write-Log ("granted: " + ($jobTools -join ' '))

# -- run -----------------------------------------------------------------------
$promptLines = @(
  'You are the desk agent running unattended.',
  ('Read tools/desk_agent/playbook.md, then tools/desk_agent/jobs/' + $Job + '.md,'),
  'and carry out that job now.'
)
if ($needsDesktop) {
  $promptLines += @(
    'If you need a chart and the CDP port is not up, call tv_launch: this launcher',
    'closes TradingView after you exit, so launching is no longer a one-way door.'
  )
} else {
  # Say it in the negative as well as the positive. This session may have no
  # desktop at all -- the task can be registered to run with nobody signed in
  # -- and an agent that reaches for tv_launch there burns its run discovering
  # that Electron has nowhere to draw, then reports a chart problem rather than
  # the instruction problem it actually hit.
  $promptLines += @(
    'This job reads bar data, NOT a chart. Do NOT call tv_launch or any TradingView',
    'tool: this task may be running with no desktop, where they cannot work.',
    'Use tools/desk_levels.py for levels and for any chart image you need.'
  )
}
$promptLines += @(
  'Append exactly one run record when you finish, as the playbook describes -',
  'including if you did nothing.'
)
$prompt = $promptLines -join ' '

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
      # For a desktop-free job that is the expected outcome rather than a
      # nothing-happened, and it is worth saying which of the two this was.
      if (-not $needsDesktop) { Write-Log 'desktop-free job: none was expected' }
    } else {
      # A desktop-free job that nonetheless left a TradingView behind ignored
      # its own instruction not to launch one. Still closed -- the rule is
      # close what this run opened, whatever opened it -- but said out loud,
      # because on a task with no desktop that launch cannot have worked and
      # whatever the run reported about a chart is not to be believed.
      if (-not $needsDesktop) {
        Write-Log 'WARNING: a desktop-free job started TradingView. It was told not to.'
        Write-Log '         Distrust anything this run said about a chart, and closing it now.'
      }
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

  # Put what the agent actually printed INTO the record. Without this the only
  # account of a failed run is a log file under LOCALAPPDATA that no cloud
  # session can read, while runs.jsonl -- the committed, reviewable half --
  # said nothing but "exit code N", which is a symptom and not a diagnosis.
  # Four consecutive run records asked for this and none could make it: the
  # agent may not edit its own log, and this is the code that writes it.
  #
  # The log's file NAME and not its path: the name carries the timestamp that
  # finds it, and the path carries a home directory into a public repository.
  # "printed nothing at all" is deliberately its own sentence -- a crash with
  # a message and a process that produced no output are different faults, and
  # collapsing them is how this system has gone wrong three times.
  $tail = Get-OutputTail $output
  if ($tail) {
    $detail = ' Output tail: ' + $tail
  } else {
    $detail = ' The agent printed nothing at all before exiting.'
  }
  $record = ('agent run failed with exit code ' + $code +
             '; full log ' + (Split-Path $logFile -Leaf) + '.' + $detail)

  Record-Failure ('claude exited non-zero: ' + $code) $record
  Publish-RunLog $branch
  exit $code
}

Publish-RunLog $branch
Write-Log 'done'
exit 0
