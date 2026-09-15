<#
.SYNOPSIS
  Launch Finviz Research in no-brainer interactive mode. This is what the
  desktop shortcut (tools\install_finviz_shortcut.ps1) points at.

.DESCRIPTION
  Finds Python (the repo's own .venv if one exists, otherwise whatever
  'python' resolves to on PATH), makes sure the packages this tool needs
  (finvizfinance, pandas, yfinance -- the watchlist "check" command reads
  real price history via yfinance, same as crypto_scan.py/season_scan.py)
  are installed, then runs tools\finviz_scan.py menu -- a plain numbered
  menu, no flags to type.

  Deliberately does NOT install the rest of requirements.txt: this repo's
  full dependency list includes heavy, unrelated packages (backtrader,
  transformers, ib_insync...) that a finviz lookup has no business waiting
  on. If a fuller dev setup is already in place (python -m venv .venv &&
  pip install -r requirements-dev.txt, per CLAUDE.md), this script uses it
  as-is and installs nothing extra.

  Also sets FINVIZ_DESKTOP to the real Desktop folder
  ([Environment]::GetFolderPath('Desktop'), which sees a OneDrive-redirected
  Desktop that Python's Path.home()/"Desktop" cannot) so saved research
  files land where you'll actually look for them.

  The window stays open on both success and failure -- PowerShell launched
  from a desktop shortcut closes itself the instant the script ends, and a
  closed window is indistinguishable from "it worked" to someone who was
  not watching for it.

  Windows PowerShell 5.1: ASCII bytes only, no bash-style command chaining,
  no '~' for home, no here-strings.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# tools\ -> repo root. Derived from where this file sits, not a hard-coded
# path: CLAUDE.md notes two checkouts exist on this machine
# (C:\Users\Gexio\OneDrive\pwb-toolbox is canonical, C:\Users\Gexio\pwb-toolbox
# also exists), and a launcher has to run the checkout it was started from.
$repoRoot = Split-Path -Parent $PSScriptRoot

try {
  if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'tools\finviz_scan.py'))) {
    throw "tools\finviz_scan.py not found under $repoRoot -- is this the pwb-toolbox checkout?"
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

  foreach ($pkg in @('finvizfinance', 'pandas', 'yfinance')) {
    Write-Host "Checking for the $pkg package..."
    & $python -c "import $pkg" 2>$null
    if ($LASTEXITCODE -ne 0) {
      Write-Host "Not found -- installing $pkg (one-time, a few seconds)..."
      & $python -m pip install --quiet $pkg
      if ($LASTEXITCODE -ne 0) {
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
  try {
    & $python (Join-Path $repoRoot 'tools\finviz_scan.py') menu
    $exitCode = $LASTEXITCODE
  } finally {
    Pop-Location
  }

  if ($exitCode -ne 0) {
    Write-Host ''
    Write-Host "finviz_scan.py exited with code $exitCode." -ForegroundColor Yellow
  }
} catch {
  Write-Host ''
  Write-Host 'FINVIZ RESEARCH FAILED TO START:' -ForegroundColor Red
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host ''
  Write-Host 'Copy everything above and send it back if you want this fixed.'
} finally {
  Write-Host ''
  Read-Host 'Press Enter to close this window'
}
