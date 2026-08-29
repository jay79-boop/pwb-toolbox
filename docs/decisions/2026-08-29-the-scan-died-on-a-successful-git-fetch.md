# The morning scan died on a *successful* `git fetch`

*Decided 2026-08-29.* Root cause of the missing desk reports first recorded in
[the desk-watch protocol](../desk-watch.md); that file said the cause was
unknown and outside this repository, and it is now known.

**The bug:** in `Scripts\spicy-desk-morning-scan.ps1` (the owner's machine, not
this repo), the opportunistic fast-forward block runs

```powershell
git fetch jay 2>&1 | Out-String | Add-Content -Path $log -Encoding utf8
```

under a file-level `$ErrorActionPreference = 'Stop'`, and **outside** the
script's `try`/`catch`.

`git fetch` writes its ordinary progress — `From https://github.com/...` — to
**stderr, on success**. In Windows PowerShell 5.1, redirecting a native
command's stderr with `2>&1` wraps each line in an `ErrorRecord`; under `Stop`
that is a terminating error. So a fetch that worked perfectly killed the script
mid-line, before the `try` that would have caught it and before any log write.

Confirmed by direct reproduction on the machine, not inferred:

```
DIED: RemoteException -- theory CONFIRMED
```

**Why it looked random.** The block only runs when the working tree is clean:

```powershell
if ($branch -eq 'main' -and -not $dirty) { ...fetch... } else { ...log line... }
```

A dirty tree took the `else`, wrote one log line, and the scan completed
normally. A clean tree reached the fetch and died. `tools/desk_agent/runs.jsonl`
is frequently modified in that checkout, so the desk appeared to work whenever
it happened to be dirty. That is the whole pattern: **2026-08-28 logged
`dirty=True` and produced a report; 08-26 and 08-27 logged nothing after the
`claude:` line and produced none.**

**The author already knew this trap.** Thirty lines below, guarding the
`claude.exe` call, the same file carries:

> Do NOT pipe stderr with `2>&1` for the native exe — PowerShell wraps each
> stderr line in an ErrorRecord under `ErrorActionPreference='Stop'`, which can
> fail a run that actually succeeded.

The guard was written for `claude.exe` and never applied to the `git` calls
immediately above it. The knowledge was present; its application was not.

**The fix**, applied on the machine 2026-08-29: save and set
`$ErrorActionPreference = 'Continue'` around the git block, restoring it at the
top of the `try`, plus `$env:GIT_TERMINAL_PROMPT = '0'` so a credential prompt
can never become an unanswerable hang in a task with no stdin. Backups kept as
`.bak` (original) and `.bak2` (pre-fix).

## Two wrong turns worth keeping

Both were confidently reasoned and both were wrong, which is the point.

1. **"The 20-minute task limit killed it."** Plausible — the task does carry
   `PT20M`, and the signature looked like a hang. The check offered to test it
   (`LastTaskResult`) *could not have*: that field reports only the most recent
   run, which was the successful 08-28 one. Asking for evidence that cannot
   discriminate is worse than asking for none, because the answer feels like
   information.
2. **"OneDrive's `gc` lock prompt hung it."** Documented in the machine skill as
   exactly this failure shape, and therefore very tempting. Ruled out in one
   command: `gc.auto = 0` is set in that checkout.

What settled it was neither theory but the primary source — four run blocks from
the log, read side by side, where the only difference between the day that
worked and the days that did not was `dirty=True`.

## The general lesson

**A "quiet" automated job and a dead one leave identical evidence.** Nothing
about the desk looked wrong: no error, no alert, no failed task — the scheduler
reported `LastTaskResult 0` and `NumberOfMissedRuns 0` throughout, because the
*trigger* never missed. The failure was entirely inside the run, and the run's
own error handling could not see it because the fault was upstream of the
`try`.

`tools/desk_watch.py` exists for this class of failure and found more than the
hand-written note had: **5 of 7 sessions missing across 2026-08-20..28, not 3.**
08-20 and 08-21 were also gone; nobody had looked back that far. It is now wired
into the top of the wrapper, auditing yesterday-and-back, so a run that dies is
reported by the next one.

Anything that runs unattended needs a check *outside* itself. That is the same
conclusion `Vault Automation\run-agent.ps1` reached independently: a script
cannot report that it failed to start.
