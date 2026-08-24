<#
.SYNOPSIS
  Audit a closed-source Electron desktop app for outbound network egress,
  credential access, and publisher identity, without running it.

.DESCRIPTION
  Written for judging "AI companion" apps that drive TradingView Desktop over the
  Chrome DevTools Protocol -- see docs/tradingview-agent-security.md for why the
  question matters. Nothing here is specific to one vendor.

  The script never launches the app. It reads the installed bundle off disk and
  reports four things:

    1. Publisher identity   - Authenticode signature and SHA256 of the binaries.
    2. Network surface      - every URL literal compiled into the bundle, bucketed
                              so the handful that are not TradingView, Anthropic or
                              boilerplate stand out.
    3. Credential surface   - whether the bundle contains code that reads cookies,
                              localStorage, session tokens or API keys.
    4. Auto-update          - whether it can replace its own code after you audit it.

  A clean report is not a proof of safety. It is a proof that the obvious ways to
  exfiltrate are not present in plain text in this build. An app that downloads
  code at runtime can pass this and still do anything -- which is why the
  auto-update section is the one to read first.

.PARAMETER Path
  Install directory or .exe to audit. Omit to auto-discover by Name.

.PARAMETER Name
  Substring matched against directory names during auto-discovery.
  Default "companion".

.PARAMETER Live
  Also snapshot outbound TCP connections owned by matching running processes.
  Run this while the app is open and idle -- an app that talks to nothing while
  idle is a better sign than any string scan.

.EXAMPLE
  .\tools\audit_electron_app.ps1

.EXAMPLE
  .\tools\audit_electron_app.ps1 -Path "$HOME\AppData\Local\Programs\TradeCompanion"

.EXAMPLE
  .\tools\audit_electron_app.ps1 -Name companion -Live
#>
[CmdletBinding()]
param(
  [string] $Path,
  [string] $Name = 'companion',
  [switch] $Live
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$report = New-Object System.Collections.Generic.List[string]
function Say([string] $line) {
  Write-Host $line
  $report.Add($line)
}
function Section([string] $title) {
  Say ''
  Say ('=' * 72)
  Say $title
  Say ('=' * 72)
}

# ---------------------------------------------------------------- discovery --

function Find-AppRoots {
  param([string] $Needle)

  $roots = @(
    (Join-Path $env:LOCALAPPDATA 'Programs'),
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)},
    $env:LOCALAPPDATA,
    (Join-Path $env:APPDATA 'Local')
  ) | Where-Object { $_ -and (Test-Path $_) }

  $hits = New-Object System.Collections.Generic.List[string]
  foreach ($root in $roots) {
    try {
      Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*$Needle*" } |
        ForEach-Object { $hits.Add($_.FullName) }
    } catch { }
  }
  # Prefer directories that actually look like Electron, but keep the rest:
  # a non-Electron match is still worth reporting rather than silently dropping.
  $electron = @($hits | Where-Object {
    (Test-Path (Join-Path $_ 'resources\app.asar')) -or (Test-Path (Join-Path $_ 'resources\app'))
  })
  if ($electron.Count -gt 0) { return $electron }
  return $hits
}

Section 'TARGET'

$targets = @()
if ($Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    Say "Path not found: $Path"
    Say 'Pass -Path pointing at the install directory or the .exe.'
    exit 1
  }
  $item = Get-Item -LiteralPath $Path
  $targets = @(if ($item.PSIsContainer) { $item.FullName } else { $item.DirectoryName })
} else {
  $targets = @(Find-AppRoots -Needle $Name)
}

if (-not $targets -or $targets.Count -eq 0) {
  Say "No installed directory matching '*$Name*' was found under:"
  Say '  %LOCALAPPDATA%\Programs, %ProgramFiles%, %ProgramFiles(x86)%, %LOCALAPPDATA%'
  Say ''
  Say 'If the app is installed somewhere else, re-run with -Path pointing at it.'
  Say 'If it is not installed yet, that is the right time to audit the installer:'
  Say '  .\tools\audit_electron_app.ps1 -Path "$HOME\Downloads\TheInstaller.exe"'
  exit 1
}

foreach ($t in $targets) { Say "Auditing: $t" }

# --------------------------------------------------------------- signatures --

Section '1. PUBLISHER IDENTITY  (who vouched for this binary)'

$exeCount = 0
foreach ($t in $targets) {
  $exes = @(Get-ChildItem -LiteralPath $t -Filter *.exe -File -ErrorAction SilentlyContinue |
            Select-Object -First 8)
  foreach ($exe in $exes) {
    $exeCount++
    Say ''
    Say ("  File     : " + $exe.Name + "  (" + [math]::Round($exe.Length / 1MB, 1) + " MB)")
    try {
      $sig = Get-AuthenticodeSignature -LiteralPath $exe.FullName
      Say ("  Status   : " + $sig.Status)
      if ($sig.SignerCertificate) {
        Say ("  Signer   : " + $sig.SignerCertificate.Subject)
        Say ("  Issuer   : " + $sig.SignerCertificate.Issuer)
        Say ("  Valid to : " + $sig.SignerCertificate.NotAfter)
      } else {
        Say '  Signer   : NONE - this binary is unsigned.'
      }
    } catch {
      Say ("  Status   : could not read signature (" + $_.Exception.Message + ")")
    }
    try {
      Say ("  SHA256   : " + (Get-FileHash -LiteralPath $exe.FullName -Algorithm SHA256).Hash)
    } catch {
      Say '  SHA256   : could not hash'
    }
  }
}

Say ''
if ($exeCount -eq 0) {
  Say '  No .exe found in the target directory.'
} else {
  Say '  READ THIS AS: "Valid" means a certificate authority has bound this file to a'
  Say '  named legal entity, and the file has not been altered since. "NotSigned" or'
  Say '  "UnknownError" means nobody has. An unsigned build is not proof of bad intent,'
  Say '  but it removes your only way to tell this build from a substituted one -- so'
  Say '  record the SHA256 above and re-check it after every update.'
}

# ------------------------------------------------------------ string mining --

function Get-BundleMatches {
  param(
    [string]   $File,
    [string]   $Pattern,
    [int]      $MaxHits = 4000
  )
  # Stream the file in chunks so a 200 MB asar does not land in memory at once.
  # A 1 KB overlap keeps a URL from being split across a chunk boundary.
  $chunk    = 4MB
  $overlap  = 1024
  $enc      = [Text.Encoding]::GetEncoding(28591)   # latin-1: byte-for-byte, never throws
  $found    = New-Object System.Collections.Generic.HashSet[string]
  $fs       = [IO.File]::OpenRead($File)
  try {
    $buf  = New-Object byte[] $chunk
    $tail = ''
    while (($read = $fs.Read($buf, 0, $chunk)) -gt 0) {
      $text = $tail + $enc.GetString($buf, 0, $read)
      foreach ($m in [regex]::Matches($text, $Pattern, 'IgnoreCase')) {
        [void] $found.Add($m.Value)
        if ($found.Count -ge $MaxHits) { break }
      }
      if ($found.Count -ge $MaxHits) { break }
      $tail = if ($text.Length -gt $overlap) { $text.Substring($text.Length - $overlap) } else { $text }
    }
  } finally {
    $fs.Dispose()
  }
  return $found
}

# Files worth scanning: the asar bundle, loose app js, and the main exe.
$scanFiles = New-Object System.Collections.Generic.List[string]
foreach ($t in $targets) {
  foreach ($rel in @('resources\app.asar', 'resources\app.asar.unpacked', 'resources\app')) {
    $p = Join-Path $t $rel
    if (Test-Path -LiteralPath $p) {
      $it = Get-Item -LiteralPath $p
      if ($it.PSIsContainer) {
        Get-ChildItem -LiteralPath $p -Recurse -File -Include *.js, *.json, *.mjs, *.cjs -ErrorAction SilentlyContinue |
          Select-Object -First 4000 | ForEach-Object { $scanFiles.Add($_.FullName) }
      } else {
        $scanFiles.Add($it.FullName)
      }
    }
  }
}

Section '2. NETWORK SURFACE  (every host this build can name)'

if ($scanFiles.Count -eq 0) {
  Say '  No Electron bundle (resources\app.asar) found under the target.'
  Say '  Either this is not an Electron app, or it ships its code elsewhere.'
  Say '  Scanning the .exe files directly instead.'
  foreach ($t in $targets) {
    Get-ChildItem -LiteralPath $t -Filter *.exe -File -ErrorAction SilentlyContinue |
      Select-Object -First 3 | ForEach-Object { $scanFiles.Add($_.FullName) }
  }
}

$urlPattern = 'https?://[a-z0-9][a-z0-9\.\-]{1,120}[a-z0-9](?::\d+)?'
$allHosts = New-Object System.Collections.Generic.HashSet[string]

foreach ($f in $scanFiles) {
  try {
    $hits = @(Get-BundleMatches -File $f -Pattern $urlPattern)
    foreach ($h in $hits) {
      $u = $h -replace '^https?://', ''
      $u = ($u -split '/')[0]
      $u = ($u -split ':')[0]
      [void] $allHosts.Add($u.ToLower())
    }
  } catch {
    Say ("  (could not scan " + $f + ": " + $_.Exception.Message + ")")
  }
}

$benign = @(
  'w3.org', 'www.w3.org', 'schemas.xmlsoap.org', 'schemas.microsoft.com',
  'nodejs.org', 'electronjs.org', 'www.electronjs.org', 'opensource.org',
  'mozilla.org', 'www.mozilla.org', 'unicode.org', 'www.unicode.org',
  'json-schema.org', 'tools.ietf.org', 'datatracker.ietf.org', 'ietf.org',
  'registry.npmjs.org', 'npmjs.com', 'www.npmjs.com', 'example.com',
  'localhost', '127.0.0.1', 'github.com', 'raw.githubusercontent.com',
  'developer.mozilla.org', 'ecma-international.org', 'creativecommons.org'
)
$telemetry = @(
  'sentry.io', 'ingest.sentry.io', 'posthog.com', 'app.posthog.com',
  'mixpanel.com', 'api.mixpanel.com', 'segment.io', 'api.segment.io',
  'amplitude.com', 'api.amplitude.com', 'google-analytics.com',
  'googletagmanager.com', 'bugsnag.com', 'datadoghq.com', 'plausible.io',
  'umami.is', 'logrocket.com', 'fullstory.com', 'heap.io', 'statsig.com'
)

$bTradingView = @(); $bAnthropic = @(); $bLocal = @()
$bBoiler = @(); $bTelemetry = @(); $bOther = @()

foreach ($h in ($allHosts | Sort-Object)) {
  if     ($h -match '(^|\.)tradingview\.com$' -or $h -match 'pine-facade|symbol-search|pricealerts') { $bTradingView += $h }
  elseif ($h -match '(^|\.)anthropic\.com$' -or $h -match '(^|\.)claude\.ai$')                        { $bAnthropic   += $h }
  elseif ($h -eq 'localhost' -or $h -eq '127.0.0.1' -or $h -match '^192\.168\.' -or $h -eq '0.0.0.0') { $bLocal       += $h }
  elseif ($telemetry | Where-Object { $h -eq $_ -or $h.EndsWith('.' + $_) })                          { $bTelemetry   += $h }
  elseif ($benign    | Where-Object { $h -eq $_ -or $h.EndsWith('.' + $_) })                          { $bBoiler      += $h }
  else                                                                                                { $bOther       += $h }
}

function Dump([string] $label, $items) {
  Say ''
  Say ("  -- " + $label + " (" + @($items).Count + ")")
  if (@($items).Count -eq 0) { Say '     (none)'; return }
  foreach ($i in $items) { Say ("     " + $i) }
}

Say ("  Scanned " + $scanFiles.Count + " bundle file(s); " + $allHosts.Count + " distinct hosts.")
Dump 'LOCAL (the CDP port and friends - expected)'      $bLocal
Dump 'TRADINGVIEW (expected for this class of tool)'    $bTradingView
Dump 'ANTHROPIC / CLAUDE (expected - your own account)' $bAnthropic
Dump 'FRAMEWORK BOILERPLATE (ignore)'                   $bBoiler
Dump 'TELEMETRY / ANALYTICS  <-- read these'            $bTelemetry
Dump 'EVERYTHING ELSE  <-- read these'                  $bOther

Say ''
Say '  READ THIS AS: the last two buckets are the whole point. A vendor domain in'
Say '  "EVERYTHING ELSE" is not automatically bad -- licence checks and update feeds'
Say '  live there too. It is bad if it appears next to a hit in section 3.'

# --------------------------------------------------------- credential surface --

Section '3. CREDENTIAL SURFACE  (what it can read that is worth money)'

$probes = @(
  @{ Label = 'reads browser cookies (document.cookie)';       Pattern = 'document\.cookie' },
  @{ Label = 'reads CDP cookie jar (Network.getAllCookies)';  Pattern = 'getAllCookies|Network\.getCookies|Storage\.getCookies' },
  @{ Label = 'reads localStorage / sessionStorage';           Pattern = 'localStorage|sessionStorage' },
  @{ Label = 'names a TradingView session token';             Pattern = 'sessionid|auth_token|tv_ecuid' },
  @{ Label = 'handles an Anthropic API key';                  Pattern = 'sk-ant-|ANTHROPIC_API_KEY|x-api-key' },
  @{ Label = 'reads the Windows credential store';            Pattern = 'CredRead|DPAPI|ProtectedData|keytar' },
  @{ Label = 'opens the CDP debug port';                      Pattern = 'remote-debugging-port|9222' },
  @{ Label = 'spawns other processes';                        Pattern = 'child_process|execSync|spawnSync|CreateProcess' },
  @{ Label = 'evaluates code fetched at runtime';             Pattern = 'new Function\(|eval\(|vm\.runIn' }
)

foreach ($probe in $probes) {
  $hitFiles = New-Object System.Collections.Generic.List[string]
  foreach ($f in $scanFiles) {
    try {
      # @() matters: a one-element result unrolls to a scalar, and .Count on a
      # scalar throws under Set-StrictMode 2.0 -- which the catch below would eat,
      # turning every probe into a silent "absent".
      $m = @(Get-BundleMatches -File $f -Pattern $probe.Pattern -MaxHits 1)
      if ($m.Count -gt 0) { $hitFiles.Add((Split-Path $f -Leaf)) }
    } catch { }
  }
  $mark = if ($hitFiles.Count -gt 0) { 'PRESENT' } else { 'absent ' }
  Say ("  [" + $mark + "]  " + $probe.Label)
}

Say ''
Say '  READ THIS AS: PRESENT is not a finding on its own. Electron bundles ship huge'
Say '  dependency trees and half of these appear in library code that never runs.'
Say '  The combination that matters is: reads a credential  +  a host in the last two'
Say '  buckets of section 2  +  no signature in section 1. Any two of those three is'
Say '  a reason to keep it away from an account that holds money.'

# ------------------------------------------------------------- auto-update ---

Section '4. AUTO-UPDATE  (does todays audit still hold tomorrow)'

$foundUpdater = $false
foreach ($t in $targets) {
  foreach ($rel in @('resources\app-update.yml', 'resources\app-update.yaml')) {
    $p = Join-Path $t $rel
    if (Test-Path -LiteralPath $p) {
      $foundUpdater = $true
      Say ("  Found " + $rel + ":")
      Get-Content -LiteralPath $p | ForEach-Object { Say ("     " + $_) }
    }
  }
}
foreach ($f in $scanFiles) {
  try {
    $m = @(Get-BundleMatches -File $f -Pattern 'electron-updater|autoUpdater|checkForUpdates' -MaxHits 1)
    if ($m.Count -gt 0) { $foundUpdater = $true }
  } catch { }
}

Say ''
if ($foundUpdater) {
  Say '  [PRESENT]  This app can replace its own code without reinstalling.'
  Say ''
  Say '  That is the single most important line in this report. It means the audit you'
  Say '  are reading has a shelf life of exactly one release. Re-run this script after'
  Say '  any version bump, and compare the SHA256 in section 1 against the one you'
  Say '  recorded last time.'
} else {
  Say '  [absent]   No auto-updater found. This build is what it will stay, so a'
  Say '  clean report here stays true until you install a new one yourself.'
}

# ----------------------------------------------------------------- live obs --

if ($Live) {
  Section '5. LIVE CONNECTIONS  (run this while the app is open and idle)'
  $procs = @(Get-Process | Where-Object { $_.ProcessName -like "*$Name*" })
  if ($procs.Count -eq 0) {
    Say ("  No running process matching '*" + $Name + "*'. Start the app, then re-run with -Live.")
  } else {
    foreach ($p in $procs) {
      Say ("  PID " + $p.Id + "  " + $p.ProcessName)
      $conns = @(Get-NetTCPConnection -OwningProcess $p.Id -ErrorAction SilentlyContinue |
                 Where-Object { $_.State -eq 'Established' })
      if ($conns.Count -eq 0) { Say '     (no established outbound connections)' }
      foreach ($c in $conns) {
        $who = try { [Net.Dns]::GetHostEntry($c.RemoteAddress).HostName } catch { $c.RemoteAddress }
        Say ("     -> " + $c.RemoteAddress + ":" + $c.RemotePort + "   " + $who)
      }
    }
    Say ''
    Say '  READ THIS AS: an idle app talking only to 127.0.0.1 and api.anthropic.com is'
    Say '  behaving as advertised. Anything else, note the host and check it above.'
  }
}

# --------------------------------------------------------------------- out ---

Section 'WHAT THIS DOES NOT TELL YOU'
Say '  This is a static read of one build. It cannot see code downloaded at runtime,'
Say '  anything obfuscated or packed, or a decision made on a server. It is a way to'
Say '  catch the careless and the obvious, not a clean bill of health.'
Say ''
Say '  The risk that survives a perfectly clean report is in'
Say '  docs/tradingview-agent-security.md: while the CDP port is open, ANY process on'
Say '  this machine can drive your logged-in TradingView. That is true of every tool'
Say '  in this class, including the open-source one, and no audit of one app fixes it.'

$dir = Join-Path $HOME 'Downloads'
if (-not (Test-Path -LiteralPath $dir)) {
  try { New-Item -ItemType Directory -Path $dir -Force | Out-Null } catch { $dir = $env:TEMP }
}
$out = Join-Path $dir 'app-audit-report.txt'
try {
  [IO.File]::WriteAllText($out, ($report -join "`r`n"), (New-Object Text.UTF8Encoding $false))
  Write-Host ''
  Write-Host ("Saved to: " + $out)
  Write-Host 'Paste sections 1, 2 and 4 back to Claude for a read.'
} catch {
  Write-Host ''
  Write-Host ("Could not save report: " + $_.Exception.Message)
}
