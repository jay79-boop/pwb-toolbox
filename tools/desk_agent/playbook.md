# Desk agent playbook

Standing instructions for every unattended run. Each job file under `jobs/`
says what that run is for; this file says how all of them behave.

Read this first, then the job file, then do the work. Append exactly one run
record at the end, always, including when you did nothing.

---

## GUARDRAILS — not self-editable

**The weekly review may rewrite any part of this file except this section.**
If a review proposes a change here, it must stop and raise it instead. That is
the whole point of drawing the line: an agent that can loosen its own limits
does not have limits.

**The account.** Run only against the TradingView login that has **no broker
connected**.

An earlier version of this section told you to open the Trading Panel each run
and confirm no broker was linked. **That instruction was unperformable and has
been removed.** The bridge exposes no tool that reports broker linkage —
`tv_ui_state` returns which panels are open, not what is connected — and the
only tool that could read it, `ui_evaluate`, is denied for exactly the reasons
that make it able to. An unattended run cannot prove that absence, and a
guardrail that cannot be carried out is worse than none: it reads as protection
while every run either abandons or quietly skips the check.

What actually holds the line is structural, and neither part depends on your
compliance:

1. **The permission system**, which denies you every tool that could reach an
   order ticket — the nine generic `ui_*` drivers, `ui_evaluate`, and
   `batch_run`. You cannot place an order because you have no means to, not
   because you were asked not to.
2. **The login itself**, which is established as broker-free once, by a human,
   at setup — and recorded in `tools/desk_agent/README.md`.

**Your obligation is to fail closed on positive evidence.** You cannot prove no
broker is linked, but you will sometimes see that one is: an account number, a
balance, a filled-order row, an order ticket in a screenshot you took for another
reason. If you see any of it, **stop immediately**. Do not continue the job. Log
the run as `failed` with blocker key `broker-connected-on-agent-login`, and say
plainly in the summary what you saw. This is not a soft warning to work around,
and "it was only in a screenshot" is not a reason to continue.

**Never place, modify or cancel an order.** Not on any account, not in any
mode, not "to test". Do not click Buy, Sell, or anything in the order ticket.
Bar replay's simulated buy/sell is fine and is not an order.

**Never connect a broker**, authorise an OAuth flow, or accept a broker linking
prompt. If a UI puts one in front of you, dismiss it and log a blocker.

**Never widen your own access.** Do not add MCP servers, edit permission
settings, change the guardrails above, or disable a check because it is in the
way. Log the obstacle and let a human decide.

**The debug port closes itself — you may launch.** `run_job.ps1` records whether
TradingView was already running before you started, and closes it afterwards if
it was not, whether you finish cleanly or crash. So `tv_launch` is no longer the
one-way door it was, and you should call it when you need a chart and the port
is down.

If the owner already had TradingView open, the launcher deliberately leaves it
open rather than destroying their session — so do not assume the port closes in
that case, and do not open anything you would not want left open.

---

## What every run does

1. **Check the guardrail precondition** above. Abandon the run if it fails.
2. **Read the last few run records** for this job so you are not rediscovering
   yesterday's blocker:
   `python -m tools.desk_agent.runlog summary --job <job> --last 5`
3. **Check that the previous record reached GitHub:**
   `python -m tools.desk_agent.runlog unpushed`
   Exit 1 means this machine holds runs the fork's `main` cannot see. Carry on
   with the job, and add `--blocker "run-log-not-pushed"` to this run's record
   so the review sees it recur. Exit 2 means it could not tell — no `jay`
   remote, as in a cloud clone — and is not a blocker. Do not push to fix it;
   see "Leave the tree clean" below for who does.
4. **Do the job**, as its file describes.
5. **Append one run record.** Always. See below.

## Recording the run

One record per run, no exceptions — a run that logs nothing is indistinguishable
from a scheduler that never fired, and telling those two apart is most of what
the log is for.

```
python -m tools.desk_agent.runlog append --job premarket --outcome ok \
    --summary "one line a human can read in a hurry" \
    --action "what you actually changed" \
    --blocker "what stopped you, if anything" \
    --metric candidates=3
```

Pick the outcome honestly:

| outcome | when |
| --- | --- |
| `ok` | the job ran and finished its work |
| `partial` | some of it worked, some was blocked |
| `failed` | it tried and broke |
| `skipped` | a precondition was not met — market closed, nothing to do |

**`skipped` and `failed` are not interchangeable.** A holiday morning is
`skipped`. A refused connection is `failed`. Getting this wrong makes a broken
system look like a quiet one, and the review reads these counts literally.

**An empty `--action` list is a fine result.** A scan that honestly found no
setup is `ok` with no actions. Do not invent an action to look productive; the
review is built to notice a job that never acts, and that signal only works if
quiet runs are recorded quietly.

Give `--blocker` a short, stable phrase. It gets slugged and counted, so
"connection refused on 9222" beats "couldn't connect just now".

## Working style

- **Do not use the owner as a test harness.** If you can check something
  yourself, check it. Ask only for what genuinely needs their hands or their
  eyes, and say which.
- **Prefer one deep run to three shallow ones.** These jobs are unattended;
  there is no one to answer a follow-up. Carry the work to a conclusion or log
  precisely what stopped you.
- **Write outcomes, not procedures.** The summary should say what is true now,
  not what you did to find out.
- **Leave the tree clean.** Commit what you changed with a message that says
  why, or revert it. Never leave a half-edit for the morning. **Do not push.**
  The launcher (`run_job.ps1`) pushes `main` to the `jay` remote after you exit
  and verifies it with `runlog unpushed`, so a run that dies before its last
  line still reaches GitHub. From 2026-08-31 to 09-01 four records were
  committed here and never pushed, and no cloud session could see them; that
  is why the push is mechanical rather than an instruction to you. The weekly
  review is the exception: it runs in the cloud, on a branch, and its job file
  says how it pushes.
- **Say when something is dead weight.** If a job has stopped earning its
  place, the review will raise it — but if you notice it mid-run, say so in the
  summary rather than maintaining it in silence.
