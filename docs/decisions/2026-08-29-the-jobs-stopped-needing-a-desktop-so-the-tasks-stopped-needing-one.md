# The jobs stopped needing a desktop, so the tasks stopped needing one

*Decided 2026-08-29.*

The direct follow-up to
[The LogonType is not the bug; the missing desktop is](2026-08-29-the-logon-type-is-not-the-bug.md),
which closed by naming this as the real answer and scoping it out:

> **Removing the GUI dependency is the real answer and is not done here.** If
> the jobs got their levels from bar data and rendered their own chart images,
> none of the above would matter and `Password` would work cleanly.

It is done here. `premarket` and `journal` read bar data through
`tools/desk_levels.py` instead of driving TradingView Desktop, and their
scheduled tasks are registered against a stored credential. Neither needs a
desktop, a sign-in, or a machine that stays awake with somebody logged on.

## What decided the shape

Put to the owner as four questions before any code was written, because the
change alters what the jobs can *see* and not just how they are scheduled.

**Both jobs, not just the mechanical one.** Converting `premarket` alone was
the smaller and safer answer, and it was rejected for a reason worth writing
down: the prize is retiring automatic sign-in, and that only lands if *nothing*
needs a desktop. With `journal` still on the GUI the machine has to sign itself
in at 16:30 anyway, and the entire auto-logon mechanism stays for one job.

**The journal's image is a faithful render, not their chart.** This was the
question with a real cost attached, and it was put plainly. A rendered
candlestick carries none of the owner's drawings or indicator settings, and for
a journal whose job is to record *what the thesis looked like when it was
framed*, that could have been the whole point of the screenshot. It was chosen
anyway, for the property a screenshot does not have: **the same bars produce
the same picture every time.** A screenshot records one screen on one
afternoon; once the layout changes there is no way back to it. A render can be
redrawn years later from the data that framed the thesis.

**yfinance, with the staleness said out loud.** Not because it is a good feed
— it is one vendor, and probably delayed. Because it is *no worse than what
this job already had*, and the honesty is now mechanical rather than
remembered. The 2026-08-29 premarket run recorded that `CME_MINI_DL` is the
**10-minute delayed** feed, so "every NQ/ES level here is 10 min stale by
construction". Every `desk_levels` run now prints the age of the last bar and
marks the output `STALE` past a threshold.

**The backtest number moved to `backtest_lab`.** Premarket step 3 used
TradingView's Strategy Tester. The same run log records why that was worth
losing: the saved copy it tested charged no commission and no slippage, so its
−2010 was a gross figure and real friction put the truth near −2280. A number
the repo can reproduce and charge costs on is strictly better than one it
cannot.

## The argument that changed, and the one that did not

The previous entry's reasoning was never wrong. A task that runs whether the
user is logged on or not gets a logon session with **no desktop**, and for a
job that drives an Electron app that converts *a job that does not run* into *a
job that runs and is wrong* — the worse of the two, because it arrives on time
looking exactly like a real result. That still holds, and `alerts` still has
it: it is off, not retired, and it still drives the chart.

What changed is only which jobs the premise applies to. So the guard was
**narrowed rather than deleted**.
`tests/test_desk_agent_launcher.py` used to forbid `-LogonType`, `S4U` and
`Password` anywhere in the scheduler's code. It now:

- forbids a stored credential for any job declaring `NeedsDesktop = $true`,
- requires every job to declare that field, so a job added later cannot inherit
  "no desktop needed" by staying silent,
- pins that `alerts` still declares `$true`,
- and keeps the S4U ban **unnarrowed**.

**S4U stays banned for every job, and for a different reason than the desktop.**
It stores no password, which reads as the safer option, but it also carries no
credentials: DPAPI-protected secrets do not decrypt and network paths are not
reachable as the user. Both remaining jobs run Claude Code, whose own stored
authentication is that kind of secret, and `journal` reads a document under
OneDrive. S4U would fail both at run time, unattended, looking like a broken
agent rather than a wrong logon type. `Password` is the supported way to run
without a desktop here.

## What this does and does not buy

**It does not remove a stored credential from the machine.** It moves one.
Automatic sign-in stores the password as an LSA secret; so does a task
registered with `-User` and `-Password`. What actually goes away is the
**live signed-in desktop** — no auto-logon, no session sitting unlocked after a
03:00 reboot, no lock-screen question about whether a chart still renders. That
is the larger exposure of the two, and it is the one that is gone.

**The conversion is declined in one command.** `-NoStoredCredential` registers
everything Interactive, and so does simply pressing enter at the password
prompt. The jobs then work exactly as they did before, needing a signed-in
machine. Nothing here is a one-way door.

**`tools/autologon.ps1` now reports per job.** Its previous version treated any
task that was not Interactive as a fault, which would have reported this whole
change as broken. It now says which tasks need a desktop, stops counting a
missing auto sign-in as a problem when none of them do, and still calls a chart
job on a desktopless logon `WRONG`. Wake timers stay a real finding either way:
a stored credential does not wake a sleeping machine.

## Left open

**The feed is one vendor and it is not audited.** `docs/backtesting.md` records
two feeds of the same index disagreeing by 284bp over eight years while
correlating 0.93 year over year. Nothing here runs `noise_floor()` against the
levels, and a level a trade is actually placed against deserves a second source
first. The job files say so; no code enforces it.

**None of this has run on the owner's machine.** It is written from a container
with no Windows host, so the PowerShell is checked by reading and by the ASCII
and structure tests, not by execution. The first real registration run is the
test, and the read-back it prints is what settles whether it worked.

**`alerts` is the last GUI job, and it is off.** If it is retired for good, the
TradingView launch/close block in `run_job.ps1`, the auto-logon machinery and
`tools/autologon.ps1` all become dead weight together. Until then they are
live, and `pine_loop` still uses the launcher's chart path on demand.
