<#
.SYNOPSIS
  Launch the Finviz Research Dashboard in no-brainer mode. This is what the
  Desktop shortcut (tools\install_finviz_shortcut.ps1) points at.

.DESCRIPTION
  Finds Python (the repo's own .venv if one exists, otherwise whatever
  'python' resolves to on PATH), makes sure the packages the dashboard needs
  (finvizfinance, pandas, yfinance, requests) are installed, then runs
  tools\finviz_dash.py -- a local web dashboard over tools\finviz_scan.py
  (watchlist board, market map, screener, per-ticker detail with charts).
  The server opens a browser tab on http://localhost:8721 and the window
  stays open showing status until you press Ctrl+C.

  Deliberately installs only the four packages above and nothing from the
  rest of requirements.txt: the dashboard is built on the Python standard
  library otherwise (zero new dependencies).

  Also sets FINVIZ_DESKTOP to the real Desktop folder
  ([Environment]::GetFolderPath('Desktop'), which sees a OneDrive-redirected
  Desktop that Python's Path.home()/"Desktop" cannot) so saved research
  files land where you'll actually look for them.

  Windows PowerShell 5.1: ASCII bytes only, no bash-style command chaining,
  no '~' for home, no here-strings.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# tools\ -> repo root. Derived from where this file sits, not a hard-coded
# path: two checkouts of this repo exist on this machine and a launcher has
# to run the checkout it was started from.
$repoRoot = Split-Path -Parent $PSScriptRoot

try {
  if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'tools\finviz_dash.py'))) {
    throw "tools\finviz_dash.py not found under $repoRoot -- is this the pwb-toolbox checkout?"
  }

  $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
    Write-Host "Using repo .venv: $python"
  } else {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) {
      throw 'No Python found (checked .venv\Scripts\python.exe and PATH). Install Python 3.10+ and try again.'
    }
    $python = $found.Source
    Write-Host "No .venv found; using system Python: $python"
  }

  foreach ($pkg in @('finvizfinance', 'pandas', 'yfinance', 'requests')) {
    Write-Host "Checking for the $pkg package..."
    $ErrorActionPreference = 'Continue'
    try {
      & $python -c "import $pkg" 2>$null
      $importExit = $LASTEXITCODE
      if ($importExit -ne 0) {
        Write-Host "Not found -- installing $pkg (one-time, a few seconds)..."
        & $python -m pip install --quiet $pkg 2>&1 | ForEach-Object {
          if ($_ -is [System.Management.Automation.ErrorRecord]) { Write-Host $_.Exception.Message } else { Write-Host "$_" }
        }
        $pipExit = $LASTEXITCODE
      }
    } finally {
      $ErrorActionPreference = 'Stop'
    }
    if ($importExit -ne 0) {
      if ($pipExit -ne 0) {
        throw "pip install $pkg failed. Check the internet connection and re-run this script."
      }
      Write-Host 'Installed.'
    }
  }

  $env:FINVIZ_DESKTOP = [Environment]::GetFolderPath('Desktop')

  Write-Host ''
  Write-Host '------------------------------------------------------------'
  Write-Host ''

  Push-Location $repoRoot
  $ErrorActionPreference = 'Continue'
  try {
    & $python (Join-Path $repoRoot 'tools\finviz_dash.py') --port 8721
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = 'Stop'
    Pop-Location
  }

  if ($exitCode -ne 0) {
    Write-Host ''
    Write-Host "finviz_dash.py exited with code $exitCode." -ForegroundColor Yellow
    Write-Host 'If the port was already in use, the dashboard is probably already'
    Write-Host 'running -- just open http://localhost:8721 in your browser.'
  }
} catch {
  Write-Host ''
  Write-Host 'FINVIZ RESEARCH DASHBOARD FAILED TO START:' -ForegroundColor Red
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host ''
  Write-Host 'Copy everything above and send it back if you want this fixed.'
} finally {
  Write-Host ''
  Read-Host 'Press Enter to close this window'
}