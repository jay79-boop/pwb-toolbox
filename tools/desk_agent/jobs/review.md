# Job: weekly review — the self-improvement loop

**Runs:** weekly. Can run in the cloud: it reads the committed run log and the
playbook, and needs neither TradingView nor the owner's disk.
**Goal:** the playbook gets better on its own, with every change reviewable and
revertible.

## Do

1. Read the evidence:
   ```
   python -m tools.desk_agent.runlog review --last 40
   ```
2. Act on what it says, in this order:

   **Recurring blockers.** Something that has stopped a job three times will
   stop it a fourth. Work out the cause and change the playbook or the job file
   so the next run does not hit it. If the cause is outside the agent's reach —
   a machine setting, a subscription, a decision — do not paper over it: write
   it in the PR body as something needing a human.

   **Dead jobs.** A job that has run repeatedly and never produced an action is
   either mis-scoped or no longer wanted. Say so and propose removing or
   narrowing it. Do not quietly keep maintaining it.

   **Trend.** If the outcome mix is regressing, find what changed. The playbook
   is in git; `git log -p tools/desk_agent/playbook.md` shows every previous
   revision and the reasoning that came with it.

3. Make the smallest edits that address what you found.
4. Commit to a branch and open a **draft PR** titled
   `desk-agent: playbook revision <YYYY-MM-DD>`.

   If this session has no GitHub tooling and cannot open one, **push the branch
   anyway** and log a blocker with key `cannot-open-pr-from-this-session`,
   naming the branch in the summary so it can be opened by hand. Do not push
   playbook changes straight to `main` to work around it: the human glance is
   the point of the PR, and skipping it to keep the job tidy defeats the whole
   arrangement. If this recurs, the review will surface it to be fixed
   properly, which is the loop working as intended.

## What may and may not be changed

**May:** this file, the other job files, and everything in `playbook.md` below
the guardrails section.

**May not — under any circumstances:**

- The `GUARDRAILS — not self-editable` section of `playbook.md`.
- `runs.jsonl`. It is the evidence. An agent that edits its own record of what
  happened cannot be reviewed, and rewriting history to make a trend look
  better is the exact failure this rule exists to prevent.
- `runlog.py`, or the tests that pin its behaviour.
- Anything under `pwb_toolbox/`. That is the library, not the agent.

If the right fix genuinely lies in one of those, **stop and say so in the PR
body**. Raising it is the correct outcome; doing it is not.

## Why a PR rather than a push

An agent editing its own instructions unattended is the part of this system
that most deserves a human glance. A draft PR costs the owner ten seconds a
week to skim, keeps every revision revertible with one command, and makes the
agent's learning history readable in `git log` rather than being something you
have to take on trust.

## Honest outcomes

Nothing recurred, nothing died, trend flat → `ok`, **no actions, no PR**. A
review that changes something every week to look busy is worse than one that
usually finds nothing, because it makes the weeks that matter invisible.
