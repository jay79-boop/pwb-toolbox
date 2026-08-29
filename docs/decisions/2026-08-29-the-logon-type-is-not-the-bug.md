# The LogonType is not the bug; the missing desktop is

*Decided 2026-08-29.*

Asked for as **"fix the LogonType so it runs without me signed in"**, after the
07:00 premarket task was found never to have fired on a machine that sleeps
overnight. The goal is right. The mechanism named would have made things worse,
and this entry exists so the next reader does not re-derive that the hard way.

## What was rejected

`Register-ScheduledTask` defaults to `LogonType = Interactive`, which runs the
task only while the owner is signed in. The apparent fix is `S4U` or `Password`
— "run whether the user is logged on or not". Both were rejected.

**A task that runs whether the user is logged on or not gets a logon session
with no desktop.** Both enabled jobs drive TradingView Desktop, an Electron app:
`premarket` reads session levels, the prior day's range and order blocks off the
chart; `journal` captures the chart at the timeframe each thesis was framed on.
Neither survives having nowhere to render.

So the change converts *a job that does not run* into *a job that runs and is
wrong*, and that is a downgrade, not a fix. A task that never fires says so in
`LastTaskResult` and leaves the log empty — the one signal this whole system is
built to read. A task that fires into a desktop-less session produces a
gameplan assembled from whatever the failure left behind, on time, looking
exactly like a real one.

`S4U` fails a second way worth recording, because it is the option that looks
safest: it stores no password, but it also carries **no credentials**. Anything
protected by DPAPI under the user's key does not decrypt, and network paths are
not reachable as the user. On a machine whose repository and trade journal both
live under OneDrive, that is not a small caveat.

## What was chosen

Make the machine sign **itself** in, so a real desktop exists and `Interactive`
is satisfied. Three conditions, which fail independently:

1. `AutoAdminLogon` — set by Sysinternals Autologon, which stores the password
   as an LSA secret.
2. `WakeToRun` on each task — added here.
3. Wake timers enabled in the power plan.

`tests/test_desk_agent_launcher.py` now forbids `-LogonType`, `S4U` and
`Password` appearing in the scheduler's code at all. A comment explaining why
not to make a change is weaker than a test that fails when someone makes it,
and this is a change that will look obviously correct to whoever tries it next.

## Two things that are not the same flag

`StartWhenAvailable` was already set and was mistaken for covering this.

- **`StartWhenAvailable`** catches a missed run up *after* something else wakes
  the machine.
- **`WakeToRun`** sets the wake timer that brings the machine up *at* the
  scheduled minute.

Only the second is worth anything for these jobs. A pre-market gameplan
delivered at 10:15, because that is when the lid was opened, is not a pre-market
gameplan; a post-close journal entry written next morning has lost the day it
was supposed to capture. Both flags are now set, because they fix different
halves — `WakeToRun` for a machine asleep, `StartWhenAvailable` for one that was
switched off.

**And `WakeToRun` reads back `True` while doing nothing** if wake timers are
disabled in the power plan. That is a property of the machine rather than of the
task, so no amount of reading the task back detects it. It is the reason
`tools/autologon.ps1` exists as a checker rather than the registration script
simply printing another flag.

## Why the checker does not set the password

`tools/autologon.ps1` reports and does not configure the sign-in. Storing the
password correctly means writing an LSA secret; Sysinternals Autologon already
does that, is published by Microsoft, and is tested. A hand-rolled equivalent
here would be untested P/Invoke against the credential store, written from a
Linux container with no Windows machine to run it on — and the skill file
already records a case where exactly that class of code failed silently
(`[byte[]]` casts that crypto APIs reject).

What the checker *does* own is the failure mode the shortcut guides create:
`Winlogon\DefaultPassword` holds the password in **plaintext**, readable by any
local user, and half the auto-logon instructions on the internet tell you to put
it there. A test pins that the script looks for it, and a second test pins that
the script never writes a password itself.

## Left open

**Locking after an unattended sign-in is off by default.** `-EnableLock`
registers it. Two reasons it is not the default: it fires on every logon
including the owner's, and whether TradingView renders a chart for capture on a
locked session is *not established*. CDP draws from the compositor rather than
the screen so it ought to hold, and the retired `alerts` job reached the chart on
runs that were almost certainly locked — suggestive, not proof. The script names
the symptom (blank captures) and the one command that undoes it.

**Removing the GUI dependency is the real answer and is not done here.** If the
jobs got their levels from bar data and rendered their own chart images, none of
the above would matter and `Password` would work cleanly. That was chosen as a
follow-up in the same conversation and is scoped separately; it changes what the
jobs can see, so it is a project rather than a config change.
