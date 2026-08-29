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

`spec_desk/reports/` held 08-23 and 08-24 and then jumped to 08-28. Three
consecutive failures, found by hand four days later while reading the log for
something else.

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
- **It cannot see the wrapper's log.** The 08-26 and 08-27 failures produced no
  output at all, and the cause lives in
  `Scripts\spicy-desk-morning-scan.ps1` and `spec_desk/morning-scan-log.txt`,
  neither of which is in this repository. This tool reports that the days are
  missing; diagnosing *why* still means reading that log.
- **It does not know about ad-hoc runs.** A report written on a closed day is
  ignored rather than flagged, because a manual weekend scan is not an error.
- **It is a detector, not a guard.** It cannot make the scan write its file. It
  turns a silent failure into a loud one, which is the half reachable from a
  repository.

## Wiring it up

The natural home is the tail of `spicy-desk-morning-scan.ps1`, where a non-zero
exit means the trail has a hole in it. Run by hand it also answers "is the desk
actually working" from any session, cloud included, which is the same job
`python -m tools.desk_agent.runlog summary` does for the unattended agent.
