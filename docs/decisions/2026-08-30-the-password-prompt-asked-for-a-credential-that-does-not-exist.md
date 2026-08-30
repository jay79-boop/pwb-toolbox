# The password prompt asked for a credential that does not exist

*Decided 2026-08-30.*

`register_desk_agent.ps1` offers to register the desktop-free jobs against a
stored credential, prompting for the Windows password. The owner pressed enter.
Asked why, on the same day:

> yes do the conversion, but I don't have the password. none was created

That is the expected state for this machine and it was already written down.
[A PIN is not the account password](2026-08-29-a-pin-is-not-the-account-password.md)
established that they sign in with a Windows Hello PIN -- a device-local
credential sealed in the TPM -- and that ARSO, not autologon, is the route that
fits. What nobody carried forward is the consequence for the *other* script: a
passwordless setup has no account password to type, so a prompt for one is not
a choice, it is a dead end.

## What the tooling actually said

The prompt offered exactly one alternative:

> Leave it blank to register them Interactive instead (needs you signed in).

which reads as giving up on the feature. And in three places -- the prompt, the
scheduler's read-back, and `autologon.ps1`'s section 3 -- the way out was named
as *supply the password*. On this machine that is advice to produce a
credential that does not exist.

The summary was worse, because it had just been rewritten. The fix in
[#170](2026-08-30-needing-no-desktop-is-not-the-same-as-not-needing-a-sign-in.md)
stopped the report claiming a conversion that had not happened, and replaced it
with:

> Nothing here is broken, but the conversion has NOT been applied

Accurate about the tasks, and wrong about the situation. It framed a complete,
deliberate configuration as half-finished work, and named the impossible fix.

## There are two complete configurations, not one

This is the thing the tooling did not model:

| | how the session exists | needs a password? |
| --- | --- | --- |
| **A** | it does not -- the task carries a stored credential | yes |
| **B** | ARSO recreates it after a restart, then locks the device | **no** |

Both are finished. **B is the only one reachable on a machine with no account
password**, and it is the one chosen here.

The practical gap it has to close is narrow, and worth stating because it makes
B obviously sufficient. The tasks already carry `WakeToRun`, so a machine that
sleeps overnight while signed in -- locked included -- wakes itself and runs the
job today. The only thing that breaks that is **a reboot with nobody signing
back in**, such as an update at 03:00. ARSO closes exactly that.

## What changed

- The prompt names the PIN case, says a passwordless account is expected rather
  than a problem, and points at ARSO with the command to run.
- The scheduler's read-back offers both routes for an Interactive desktop-free
  task instead of only the password.
- `autologon.ps1` does the same in section 3, and its summary now presents A
  and B as two complete answers rather than describing an unfinished
  conversion. It still says the ARSO toggle was not verified, because it still
  cannot read it.

## What the tests are worth

Four breaks convict: removing ARSO from the summary's Interactive branch,
deleting the "NO PASSWORD NEEDED" statement, reverting the prompt to its
original wording, and restoring the password-only advice in the read-back.

One of them is deliberately not anchored to a phrase. The dead end existed in
**three** copies, so fixing one would have left two; `test_no_advice_anywhere_
makes_the_password_the_only_way_out` scans both scripts for any instruction to
supply a password and requires ARSO named within a few lines of it. That is the
guard against the same bug growing a fourth copy somewhere else.

A previous test in this file had to be retired to make this change: it required
the summary to say the conversion "has NOT been applied". That assertion was
correct when written and became a way of enforcing the wrong framing. It is
replaced rather than deleted -- the replacement still forbids silence, and now
also requires both routes to be offered.

## Left open

**The ARSO toggle is still unreadable from a script**, so nothing here verifies
that route is on. The report says so instead of guessing. Proof is the next
07:00 run appearing in the log after an overnight reboot.

**Still nothing here executes PowerShell.** Same limit as every entry in this
series: checked by reading and by structural tests, not by running it. The two
bugs this file records were both found by the owner running the script.
