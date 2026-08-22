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

.EXAMPLE
  .\tools\desk_agent\run_job.ps1 -Job premarket
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('premarket', 'alerts', 'journal', 'pine_loop', 'review')]
  [string] $Job,

  [string] $RepoRoot
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) { $RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
if (-not (Test-Path -LiteralPath $RepoRoot)) { throw "Repo root not found: $RepoRoot" }

$logDir = Join-Path $RepoRoot 'tools\desk_agent\logs'
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

if (-not $claude) {
  Write-Log 'Claude Code not found (looked for claude.exe then claude.cmd).'
  Record-Failure 'claude executable not found on PATH' `
                 'launcher could not resolve Claude Code, job never started'
  exit 1
}
Write-Log ("claude: " + $claude)

# -- run -----------------------------------------------------------------------
$prompt = @(
  'You are the desk agent running unattended.',
  ('Read tools/desk_agent/playbook.md, then tools/desk_agent/jobs/' + $Job + '.md,'),
  'and carry out that job now.',
  'Append exactly one run record when you finish, as the playbook describes -',
  'including if you did nothing.'
) -join ' '

Push-Location $RepoRoot
try {
  $output = $prompt | & $claude -p 2>&1 | Out-String
  $code = $LASTEXITCODE
} catch {
  $output = $_.Exception.Message
  $code = 1
} finally {
  Pop-Location
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
