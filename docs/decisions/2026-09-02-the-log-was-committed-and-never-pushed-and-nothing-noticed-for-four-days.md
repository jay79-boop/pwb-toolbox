# The log was committed and never pushed, and nothing noticed for four days

*Decided 2026-09-02.*

**Decision:** the desk agent's launcher pushes the run log after every job,
whatever way the job ended, and verifies the push by asking git. The playbook
tells the agent *not* to push. A new `runlog unpushed` command reports when this
machine holds run records the fork's `main` cannot see, and the next run logs
that as a blocker so the review sees it recur.

## What happened

The playbook's "leave the tree clean" rule says: *commit what you changed with a
message that says why*. It never said push. Every scheduled job did exactly what
it was told:

| commit | when (owner's machine) | what |
| --- | --- | --- |
| `b4fbe61` | 2026-08-31 07:05 | premarket note: no gameplan, the job cannot run its own data step |
| `28916ff` | 2026-08-31 07:05 | premarket run record |
| `035b2d8` | 2026-08-31 16:36 | journal note: the register is in the browser, not in a file |
| `4201497` | 2026-09-01 07:05 | premarket note: still no gameplan, two more walls behind the first |
| `a163c5f` | 2026-09-01 07:06 | premarket run record |
| `7db2819` | 2026-09-01 16:34 | journal run record |

Six commits on the OneDrive checkout's local `main`. GitHub's copy of
`runs.jsonl` ended at the 08-28 alerts run. The cloud — where the weekly review
runs, and where every session that is not on the owner's machine reads the log —
could not see a single one of those runs. Two of them raised a permission gap
that fails every premarket run at step 1, and the request for a one-line
settings fix sat unread on a disk for four days.

The owner found it by hand on 2026-09-02, while a handed-over `--ff-only` merge
refused on a `main` that could not fast-forward, and pushed the backlog as merge
commit `8c822da`.

## Why nothing noticed

`CLAUDE.md` says the run log is committed *because it is the only part of the
agent a cloud session can see*. That sentence carries an unstated step. A commit
is a fact about one checkout; a cloud session reads GitHub. Between the two is
a push that nothing owned.

The README came close to owning it. Its section on the review Routine says the
Routine's prompt makes it *append a record, commit it, push it*. That is true of
the review, which runs in the cloud on its own branch, and it was read — by the
session that wrote the ledger row for this finding, and by this record's author
before reading the files — as covering the three local jobs too. It never did.
`run_job.ps1` invoked the agent and, when the agent exited non-zero, wrote a
failure record; it did not push either.

And there was no check. `runlog summary` reads the local file, so on the machine
it reported four fresh runs; from the cloud it reported the log as of 08-28 and
had no way to know anything was missing. Both were telling the truth about what
they could see. The same shape as
[a written rule with no check behind it](2026-08-24-a-written-rule-with-no-check-behind-it-lasted-eight-hours.md)
and as `desk_watch.py`'s origin story: a silent failure that leaves the same
evidence as a quiet week.

## Where the push goes, and why the launcher

Two places could do it. The agent has a `git push` grant already, so the
one-line fix is a sentence in the playbook. It was rejected for the launcher,
for three reasons, in order of weight:

1. **An instruction to the agent covers only the runs the agent completes.**
   The launcher writes the record for a run that crashed or never started —
   Claude Code not found, a non-zero exit, a branch without the agent on it.
   Those are the records that most need to reach GitHub, and no playbook line
   reaches them. The launcher pushes after the agent exits, on every exit path,
   so the coverage is the same as the record-writing it already does.
2. **A headless run has nobody to answer a prompt.** The last four run records
   are about exactly this: a command the job depended on was not on the allow
   list, and `claude -p` cannot ask. The grant exists today; the launcher does
   not depend on it staying.
3. **The verification lands in the same place as the action.** The launcher
   runs `runlog unpushed` straight after the push and writes the answer into
   the run's log. `register_desk_agent.ps1`'s rule applies: the printed line is
   not evidence, the read-back is.

The playbook now says *do not push* and names the launcher, so a future review
does not "fix" the missing instruction and set two pushes racing. The review job
keeps its own push: it runs in the cloud, on a branch, and its file already says
how.

## What the launcher does, in order

`Publish-RunLog` in `run_job.ps1`:

1. **Refuse if `runs.jsonl` is missing.** `git add` of a tracked file that is
   gone stages its deletion, and that is commit `dd6d1d6` with a scheduler
   behind it.
2. **Commit what the run left behind** in `runs.jsonl` and `out/`, by naming
   those two paths. Never `git add -A`. Usually a no-op: the agent commits its
   own work, and this catches the launcher's own failure record.
3. **On `main`, fetch and merge `jay/main` first.** `main` moves on GitHub
   every time a pull request merges, and a push from behind is refused as
   non-fast-forward — without this step the fix would fail on most days while
   looking like it ran. It is the line the owner runs by hand
   (`docs/local-checkout.md`). A conflict is aborted and reported; an
   unattended task must never leave a half-merge for the morning. `gc.auto` is
   forced off on the command line because a fetch that stops to ask about a
   OneDrive-locked object directory is an unanswerable hang from a task with no
   stdin.
4. **Push to `jay`.** By name. `origin` is upstream in the OneDrive checkout
   and the fork in the other, and a bare `origin` push is wrong in one of them.
5. **Verify with `runlog unpushed`** and write `pushed:` or `NOT PUSHED` into
   the log.

One more line makes step 3 succeed in the case it would otherwise always fail.
`runs.jsonl` is appended to from two checkouts — the scheduled jobs on the
machine, the weekly review in the cloud — and with git's default driver every
such pair conflicts at the end of the file. `.gitattributes` now gives the log
`merge=union`, which keeps both sides' lines. For a file whose every line is an
independent record that is the correct merge, not a shortcut; order may
interleave and nothing reads it positionally.

Everything in it runs under `$ErrorActionPreference = 'Continue'`, because
native git writes progress to stderr and under `Stop` a redirected stderr line
is a terminating error. The record is already written by then; the publish step
may fail, and must not turn a logged run into a crashed one.

## The check

`python -m tools.desk_agent.runlog unpushed` compares the working copy of the
log with the copy on `jay/main` — the remote-tracking ref as of the last fetch,
with `--fetch` to ask GitHub first — and reports the commits touching the log
that the ref lacks, the records it cannot see with the oldest and newest named,
how many of those are not even committed, and separately how many records the
ref has that this file lacks, which is *behind* and not *ahead*. Exit codes: 0
pushed, 1 not pushed, 2 could not tell. The third exists so that a clone with no
`jay` remote — every cloud clone — reads as "no idea" rather than as clean.

Run against the real history it convicts the real incident: with the ref set to
GitHub's `main` as it was before the owner's merge, it reports four commits, four
records, oldest 2026-08-31 07:05 premarket, newest 2026-09-01 16:34 journal.
The tests mock git and pin that same case, plus the acquittal, the
written-but-uncommitted case, the behind-not-ahead case, and the missing ref.

The playbook's step 3 has every run call it, and log `run-log-not-pushed` when
it answers 1. That is the loop noticing its own gap — the design's stated
intent, which for this gap had no instrument.

## What was exercised, and what is not proven

CI is Linux and never executes `run_job.ps1`. The tests read the source: the
push exists, it names `jay`, every exit path publishes, the failure record is
written before the publish, the paths are named and `-A` is absent, the merge
is aborted on conflict, the verification follows the push. They fail against
the previous launcher, which had none of it.

Beyond the tests, the session that wrote this ran `Publish-RunLog` itself,
under PowerShell 7.4 on Linux, against a scratch bare "fork" with a `jay`
remote and a second clone standing in for the cloud. Five cases, each checked
by reading the fork's copy of the log afterwards rather than the launcher's own
output: a record the agent committed (pushed); the fork's `main` moved by a
merged PR (fetched, merged, pushed); a record the launcher wrote and nobody
committed (committed, pushed); both sides appending a record (conflict and a
clean abort without the union attribute, a clean merge and push with it); and
`runs.jsonl` deleted from the checkout (refused, nothing staged). The whole
launcher also parses with zero errors under that PowerShell.

That is not Windows PowerShell 5.1, and two things in particular are only
argued here:

- **The push authenticates.** The task runs as the owner, with a stored
  credential on the desktop-free jobs — the reason `S4U` is banned — and the
  owner's own pushes from that machine succeed, so the credential manager
  should serve the launcher the same way. The first scheduled run settles it.
- **The merge is safe on a working tree other sessions use.** The launcher
  merges only when the checkout is on `main`, and git refuses a merge that
  would overwrite uncommitted changes, which is the failure mode that matters;
  the launcher then logs it and pushes nothing. A feature branch is pushed as
  itself, without a merge, and a rejection there is logged.

The first real evidence is the next scheduled run's log under `%LOCALAPPDATA%`,
which should end with `pushed: jay/main carries the run log`, and a
`runlog unpushed --remote origin` from any cloud session afterwards, which
should say the same thing about the same records.
