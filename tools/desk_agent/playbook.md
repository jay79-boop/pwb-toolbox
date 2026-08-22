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
connected**. Before doing anything else, confirm this: open the Trading Panel
and check whether a brokerage account is linked.

- No broker linked → proceed.
- A broker is linked → **stop**. Do not continue the job. Log the run as
  `failed` with blocker key `broker-connected-on-agent-login`, and say plainly
  in the summary that the agent login has a broker on it and the run was
  abandoned. This is not a soft warning to work around.

**Never place, modify or cancel an order.** Not on any account, not in any
mode, not "to test". Do not click Buy, Sell, or anything in the order ticket.
Bar replay's simulated buy/sell is fine and is not an order.

**Never connect a broker**, authorise an OAuth flow, or accept a broker linking
prompt. If a UI puts one in front of you, dismiss it and log a blocker.

**Never widen your own access.** Do not add MCP servers, edit permission
settings, change the guardrails above, or disable a check because it is in the
way. Log the obstacle and let a human decide.

**Close down after yourself.** If you launched TradingView with the debug port
open, close it when the job ends. The port has no authentication, and leaving
it open all day is the risk `docs/tradingview-agent-security.md` is about.

---

## What every run does

1. **Check the guardrail precondition** above. Abandon the run if it fails.
2. **Read the last few run records** for this job so you are not rediscovering
   yesterday's blocker:
   `python -m tools.desk_agent.runlog summary --job <job> --last 5`
3. **Do the job**, as its file describes.
4. **Append one run record.** Always. See below.

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
  why, or revert it. Never leave a half-edit for the morning.
- **Say when something is dead weight.** If a job has stopped earning its
  place, the review will raise it — but if you notice it mid-run, say so in the
  summary rather than maintaining it in silence.
