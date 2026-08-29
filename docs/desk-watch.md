# desk_watch — did the desk actually report?

`tools/desk_watch.py` answers one question: **which trading sessions left no
record?**

## Why it exists

The spicy desk's product is not its trades — it has never opened one. Its
product is the record. Between 2026-08-25 and 2026-08-27 that record stopped
being written, and nothing said so:

- **2026-08-25** — the morning scan produced a complete report *in its log*
  (three plans on HOOD/DKS, a reasoned MRNA rejection) and never wrote the
  file. The wrapper's own last line was
  `SKIPPED vault append: no report file at ...2026-08-25.md`.
- **2026-08-26** — run header, python path, claude path, then nothing.
- **2026-08-27** — the same: zero output.

`spec_desk/reports/` held 08-23 and 08-24 and then jumped to 08-28. Found by
hand four days later while reading the log for something else.

**That account, written from the daily note, undercounted.** The first live run
of this tool against the real folder found **5 of 7 sessions missing across
2026-08-20..28**: 08-20 and 08-21 were gone as well, and nobody had looked back
far enough to see them. 08-23 is a Sunday, so it is not a session at all and is
correctly ignored here. The desk produced two reports in two weeks.

The reason it went unnoticed is the whole design problem: **a scan that fails
silently looks exactly like a quiet market.** Both leave the same evidence —
no plans, nothing to do. Only the calendar can tell them apart, and nothing was
checking the calendar.

## What it does

Walks the trading calendar over a window and names every session with no usable
report.

```bash
python tools/desk_watch.py check                    # last 10 sessions
python tools/desk_watch.py check --since 2026-08-20
python tools/desk_watch.py calendar --year 2026     # what it thinks is closed
```

`check` exits **1** when any session is missing or empty, so a wrapper can act
on the exit code instead of parsing output. `--today` fixes the reference date,
which is what makes the whole thing testable without touching a clock.

## The three states it separates

| State | Meaning |
| --- | --- |
| `ok` | a report exists and carries content |
| `MISSING` | the session ran and no file was written — the 08-25 failure |
| `EMPTY` | a file exists but is under 200 characters — the wrapper touched it and the agent never filled it |

`EMPTY` is deliberately not folded into `ok`. A file created by the wrapper and
never written to is precisely the failure this tool exists to catch, and
counting its existence as success would hide it.

## The calendar

Derived from rules, never a table, so it needs no annual upkeep — mirroring
`Scripts/nyse-holidays.ps1` on the owner's machine. Ten full closures, weekend
observance shifting, Good Friday from Gauss's Easter algorithm.

One exception is worth knowing because it is easy to get wrong and it inverts
this tool's verdict: **a Saturday New Year's Day does not shift.** Every other
holiday landing on a Saturday is observed the preceding Friday, but the
exchange trades that December 31. Applying the general rule would invent a
closure — next on 2028-12-31 — and a genuinely missing report on that day
would be silently excused as a holiday. Pinned by
`test_a_saturday_new_year_is_the_one_holiday_that_does_not_shift`.

Half-days (the day after Thanksgiving, July 3rd, Christmas Eve) are **not**
closures. The desk is expected to report on a half day; per the owner's
standing instruction they change what the report leads with, not whether it
runs.

## What it does not claim

- **It does not read the report.** A file of the right size passes. It cannot
  tell a good scan from a lazy one — only that something was written.
- **It cannot see the wrapper's log.** It reports that a session left no
  report; it cannot say why. The wrapper and its log both live on the owner's
  machine, outside this repository, so a root cause still means reading them.
  For the 2026-08 outage that has since been done, and the answer was a
  *successful* `git fetch` terminating the script through `2>&1` under
  `ErrorActionPreference = 'Stop'` --
  `docs/decisions/2026-08-29-the-scan-died-on-a-successful-git-fetch.md`.
  Note what that means for this tool's own numbers: **5 of 7 sessions were
  missing across 2026-08-20..28, not the 3 the daily note recorded** -- 08-20
  and 08-21 were gone too and nobody had looked back far enough to see them.
- **It does not know about ad-hoc runs.** A report written on a closed day is
  ignored rather than flagged, because a manual weekend scan is not an error.
- **It is a detector, not a guard.** It cannot make the scan write its file. It
  turns a silent failure into a loud one, which is the half reachable from a
  repository.

## Wiring it up

It is wired into `spicy-desk-morning-scan.ps1` (2026-08-29) — at the **top**,
auditing the last 10 sessions up to **yesterday**, appending to the scan's own
log. Both choices were corrected from the obvious ones after reading the file:

- **Not the tail.** That wrapper has seven `exit` points, and one of them is an
  `exit 0` in the exact branch that fires when the report is missing. A check
  appended at the end would have been dead in the case it exists for.
- **Not today.** A run that dies cannot report that it died — the 08-26 and
  08-27 runs left no trace at all. Auditing yesterday-and-back means a gap is
  reported by the *next* run, which is the only run still alive to report it.

Run by hand it also answers "is the desk actually working" from any session,
cloud included — the same job `python -m tools.desk_agent.runlog summary` does
for the unattended agent.
