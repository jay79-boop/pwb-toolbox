# Starting karaoke: the icon, and what it refuses to let go wrong

The protocol -- how the draw works, why the wait promise is relative, how the
QR is drawn -- is `docs/karaoke-rotation.md`. This file is only about getting
the thing running in a living room or a venue, by someone who is not going to
open a terminal.

## The icon

Run this once, in PowerShell, from the checkout:

```powershell
.\tools\karaoke_server\install_shortcut.ps1
```

It puts a **Karaoke** icon on the Desktop and prints its full path. From then
on karaoke night is one double-click: the server starts, the big screen opens
itself, and the join address is printed large enough to read out to a room.

Running the installer again updates the same shortcut rather than making a
second one. `-Remove` deletes it. `-Port 9000` bakes a different port into it.

Two details in the shortcut are load-bearing:

- It targets `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <launcher>`
  rather than the `.ps1`. Windows does not *run* a `.ps1` on double-click, it
  opens it in Notepad; and this machine's execution policy blocks unsigned
  scripts, so a shortcut without `Bypass` dies with a red wall of text that
  reads exactly like karaoke being broken.
- The launcher works out the checkout from `$PSScriptRoot`, so the icon follows
  whichever of the two `pwb-toolbox` clones it was installed from. Nothing is
  hard-coded to a user folder.

The installer reads the shortcut back off disk and compares it with what was
asked for before saying it worked. Its own message is not evidence -- OneDrive
Desktop backup can redirect the folder out from under `Save()`.

## What the launcher does about the four things that went wrong

On 2026-09-02 starting karaoke by hand failed four separate ways in one
sitting, and every one of them looked like the *product* was broken rather
than the command. One guard each:

| What went wrong | What the launcher does |
| --- | --- |
| a stale checkout | runs the code sitting next to the icon, and stops with a plain sentence if the folder was moved out of the checkout |
| a VPN address in the QR | asks the **server** which address phones can reach (`--print-address`), never a second copy of the ranking |
| Windows Firewall | checks for the rule and prints the exact one-line fix; adds it directly only when the window is already elevated and only after asking |
| an empty `Read-Host` into a flag | no answer typed at a prompt ever becomes an argument, and an empty address can never reach a URL |

### The firewall

The launcher looks for an inbound allow rule named **Karaoke Queue** covering
the port. The name is `FIREWALL_RULE_NAME` in
`tools/karaoke_server/queue_server.py` and a test fails if the two ever drift:
a check looking for one name while the fix creates another reads as "already
allowed" forever. A rule for the *wrong port* does not count, which is why the
check goes through `Get-NetFirewallPortFilter` rather than stopping at the
name.

The check has **three** answers, not two: `ok`, `missing`, and `unknown`. A
check that cannot run must not answer "missing" and must not answer "ok" --
`-ErrorAction SilentlyContinue` turning a failed check into a clean pass has
cost this project real time before.

When the rule is missing the launcher prints exactly this and carries on:

```powershell
New-NetFirewallRule -DisplayName 'Karaoke Queue' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8772 -Profile Any
```

**It never elevates on its own.** A double-clicked icon that raises a UAC
prompt and rewrites the host firewall is not a thing to ship. If the window
*is* already running as administrator it offers to add the rule, and an empty
answer means yes -- but the answer is only ever compared, never passed on as
an argument.

Karaoke still starts either way. The big screen on this machine works with no
rule at all; it is only the phones that cannot reach in.

### A busy port

Detected before anything else starts, and reported as
*"Karaoke is already running -- close the other karaoke window first."*

The same message lives in `serve()` rather than only in the launcher, so
`python -m tools.karaoke_server.queue_server` and the one-file
`karaoke_os.py` get it too. `serve()` returns `1` on a refused port instead of
raising, and the one-file build cancels its browser timer when that happens --
opening `/screen` anyway would land on the karaoke *already* running and make
"close the other window" read as a lie.

### Nothing left holding the port

The server is started with `-NoNewWindow`, so it stays attached to the
launcher's console and Windows delivers the close event to it when the window
is shut. A `finally` block stops it as well. An orphaned python is what makes
the *next* double-click say "already running" when nothing is.

### Where singer memory goes

`%LOCALAPPDATA%\karaoke\karaoke-profiles.json`, not the checkout. Memory
written into the repo shows up in `git status` and eventually in a commit, and
it belongs to the machine rather than to whichever of the two clones the icon
points at. `karaoke-profiles.json` is also gitignored, because running the
server by hand still defaults it to the working directory.

### Turning song search on (optional, and the night runs fine without it)

By default the phone takes a pasted YouTube link or a typed title, which is
all it ever did. Set `YOUTUBE_API_KEY` in the environment the server starts
in and the phone additionally gets a search box: type a song name, tap a
result. The server prints which of the two it is running as its third line.

Getting a key: Google Cloud console, enable **YouTube Data API v3**, then
Credentials -> API key. It is free, and it is capped -- roughly **100
searches a day for the whole venue**, shared, because the free 10,000
daily quota units cost 100 per search. Repeat queries are cached for the
life of the process, so the cap is less painful than it sounds.

Nothing breaks without it, and nothing breaks when it fails mid-night: a
missing key, a dead uplink, a captive portal or a spent quota all leave the
phone with the paste-a-link box it already had and one sentence saying so.
The reasoning is in `docs/karaoke-rotation.md`.

## The travelling copy

For a venue machine with no repo -- and no Python -- the same OS ships as one
file:

```bash
python tools/karaoke_server/build_standalone.py --out dist/karaoke_os.py
python dist/karaoke_os.py --selfcheck
```

`karaoke_os.py` is stdlib-only, carries the page and the QR encoder inside it,
opens `/screen` by itself 1.5s after starting, and the release workflow freezes
it into `KaraokeQueue.exe`. It is **generated, never hand-edited**: the build
is mechanical concatenation of the tested modules, so the shipped file and the
tested code stay the same code.

How it differs from the icon path:

- **No launcher and no `.ps1`.** It is `python karaoke_os.py`, or a
  double-clicked `.exe`. So there is no Python-not-found message -- if Python
  is missing there is nothing to print it, which is what the frozen `.exe` is
  for.
- **Port-in-use and the firewall command reach it anyway**, because both live
  in `serve()`, which travels inside the file.
- **Singer memory lands next to the program**, not under `%LOCALAPPDATA%` --
  a USB stick carried to a venue should carry the room's history with it.

## Reading this on Linux

`tests/test_karaoke_launcher.py` reads both `.ps1` files as text and asserts
the things that break silently: ASCII-only **bytes** (one non-ASCII byte in a
BOM-less `.ps1` makes PowerShell 5.1 fail to parse the whole file and print
nothing at all), no bash-style chaining, no `~` for home, no here-strings, no
hard-coded user path, no `-Host` parameter (`$Host` is an automatic variable),
the firewall three-state, the busy-port sentence, and `-ExecutionPolicy Bypass`
in the shortcut. Nothing executes PowerShell -- CI is Linux. The first person
to run these for real is whoever double-clicks the icon.

## What has actually been tested, and what has not

Both `.ps1` files are written and reviewed on Linux, so be precise about
what that buys.

**Verified by execution.** `tests/test_karaoke_launcher.py` hands both files
to a real PowerShell parser (`pwsh`, which GitHub's Ubuntu runners ship), so
a file the language will not accept fails CI rather than the night. The test
was confirmed to convict: a planted `if ($true) {` with no closing brace
fails it and names the line. Beyond parsing, the launcher's own functions
were pulled out by AST and run against the real world on Linux:
`Test-PythonCandidate` returns true for a genuine Python 3.11, false for a
missing command, false for `/bin/echo`, and false for a stand-in for the
**Microsoft Store stub** (exits 9009, prints nothing) — the case the check
exists for. `Test-PortBusy` and `Test-PortAnswers` were run against a real
listener, free and held. `Get-FirewallState` was exercised through all eight
branches with the Windows cmdlets stubbed: no rule, right rule, wrong port,
`Any` port, disabled, outbound-only, a Block rule, and the cmdlet throwing —
each returning the intended `ok` / `missing` / `unknown`.

**Not verified, and the owner is the first to run it.** Parsing under
PowerShell 7 is not running under Windows PowerShell 5.1. Untested: Python
discovery against a real Store stub and a real `py -3`; the elevated
firewall-add path; `WScript.Shell.CreateShortcut` writing the `.lnk`, and
whether `-ExecutionPolicy Bypass -File` defeats this machine's execution
policy on double-click; whether `[Environment]::GetFolderPath('Desktop')`
returns the OneDrive-redirected Desktop; and — the least-proven claim in the
whole change — **whether closing the window really kills the server.** Ctrl+C
is covered by the `finally` block; the window-close path rests on console
attachment semantics that cannot be exercised from a container. If a second
double-click ever reports "already running" when nothing is, that is the
guarantee that failed, and the fix is to kill python by hand once and say so.


### And the same honesty about song search

**Verified.** The search endpoint, its cache, and every failure it can meet
are driven through the real `do_GET` on a socketless handler with a fake
fetcher, plus the parser against captured-shape payloads: escaped titles,
half-broken rows, a captive portal's HTML, a 403 quota body, a timeout. A
real browser walk on Chromium queued a song by tapping a stubbed search
result, and the same walk with the key removed proved the phone still joins
and queues by typing a title, with no search box shown at all.

**Not verified: the live path.** Nobody has run a single real request
against `googleapis.com` from here, because there is no key to run it with.
So the request shape is written from the API's documentation and asserted
against the URL we build, not against an answer YouTube gave. The first
real search is the owner's. If it comes back empty or refuses, the thing to
read is the raw JSON: `curl` the same URL with the key and look at
`error.errors[0].reason` — `keyInvalid`, `accessNotConfigured` (the API is
not enabled on that project) and `quotaExceeded` are the three, and all
three currently look identical from the phone, which is by design for a
singer and unhelpful for whoever is setting it up.
