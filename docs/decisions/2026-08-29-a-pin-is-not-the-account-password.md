# A PIN is not the account password

*Decided 2026-08-29. Supersedes part of
[The LogonType is not the bug; the missing desktop is](2026-08-29-the-logon-type-is-not-the-bug.md),
which named the wrong mechanism for the first of its three conditions.*

That entry concluded the machine should sign itself in, and listed
`AutoAdminLogon` — set with Sysinternals Autologon — as how. The handover said
so, and `tools/autologon.ps1` shipped saying so.

The owner's next message was: *"I don't remember if I use a password for login.
I log in using number pattern."*

## Why the advice could not have worked

A Windows Hello PIN is a **device-local credential sealed in the TPM**. It
unlocks a secret already present on that machine. It is not the account
password, it is not derived from it, and it cannot stand in for it.

Sysinternals Autologon needs the real password. So the instruction would have
sent the owner hunting for a Microsoft-account password they had never typed, to
enable something they turn out not to need. On current builds it would not even
have reached that point: the password field is hidden until
`DevicePasswordLessBuildVersion` under
`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device` is set
to `0`.

The failure is not that a PIN is unusual. It is that a plan was written for the
machine without checking how the machine is signed into, and the checker then
repeated the same assumption in code — so the tool built to catch a wrong belief
carried it.

## What replaces it

**Automatic Restart Sign-On (ARSO).** It signs the last user back in after a
restart or cold boot, rehydrates the session from secrets the LSA persisted, and
**locks the device immediately**.

That is a better answer than autologon on every axis that matters here:

| | autologon | ARSO |
| --- | --- | --- |
| works with a PIN | no | yes |
| password stored | yes, an LSA secret | none |
| state after boot | desktop **unlocked** | desktop **locked** |
| desktop exists for the tasks | yes | yes |

The unlocked desktop is the part worth dwelling on. The original entry treated
"lock the screen afterwards" as an optional extra, `-EnableLock`, off by default
and carrying a warning about chart capture. ARSO makes it unnecessary: locking
is what the feature does, so the awkward opt-in switch stops being the only way
to avoid leaving the machine open.

Route B is still reported by the checker, second and labelled as needing the
password. It is the fallback if ARSO cannot be made to work.

## What the checker may not claim

The per-user ARSO consent — the Settings toggle — **is not readable from a
script**. Only the policy that can block ARSO outright, under
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`, can be read.

So the checker reports the policy, points at the toggle, and says plainly that
it did not verify it. Its summary no longer prints `All clear`, because a run
with zero problems has still not established that the machine signs itself in.

This is the third time in two days that this system has gone wrong by letting an
unchecked thing read as a passing one — after the read-back that printed a line
from the source file having queried nothing, and the absent CI run that looked
like no failure rather than no evidence. The rule earned by all three: **a check
that cannot verify something must say so, in the summary, where it cannot be
missed.**

The real proof is the next 07:00 run appearing in `runs.jsonl` after an
overnight reboot. Nothing readable from a script substitutes for it.

## The habit to keep

Ask how the machine is signed into before writing a plan that depends on it.
One question — asked before the handover rather than after — would have replaced
the entire first version of that row.
