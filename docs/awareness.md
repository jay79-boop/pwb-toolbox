# The situational awareness layer

`tools/awareness.py` answers seven questions at any moment:

```
what is happening now / why / what is changing / what is likely next /
what is connected to it / what deserves attention / what action is safest
```

Commissioned 2026-09-02. Those seven lines are a rendering spec, not an
architecture — underneath they demand three different machines, and separating
them is most of the design.

| Question | What it actually needs |
|---|---|
| now, connected | live reads and a declared graph — **mechanical** |
| changing, likely next | **history**; you cannot diff against nothing |
| deserves attention, safest action | **judgement** |

## What it does not do, and why that is the point

**It does not conclude.** The answers are delivered inside a Claude session —
the start-of-session catch-up, and on demand — so a reasoner is already present
and costs nothing extra. A tool that also guessed at *why* would be a second
opinion nobody asked for, and an untestable one. The core assembles evidence
and stops.

**It does not store state.** This is the rule `CLAUDE.md` already carries under
*The ledger*, and the reason a dashboard was retired on 2026-08-29
([decision](decisions/2026-08-29-retiring-the-live-work-dashboard.md)). What it
records instead is *observations*:

> "At 14:03, the run log's newest record for `journal` was a failure."

That is true forever. "The current branch is X" is false in an hour. Deltas
come from diffing a fresh derivation against the observation log, so no line in
the system claims to be current except at the moment it is asked. The log lives
in `awareness/`, gitignored — it would collide on every branch if tracked, and
a tracked always-growing file made the layer report its own output as
uncommitted work on the very first run.

## What it refuses to claim

Four refusals, in the house pattern of *refuse rather than repair*:

- **No history is reported as unanswerable, never as calm.** A layer that
  reported "nothing changed" because it had never looked before would be worse
  than one that reported nothing at all.
- **Nothing is projected without a rule.** Two rules exist: a failure streak
  whose blocker is still live projects the next run failing the same way, and a
  scheduled job projects its next run time. Anything else gets no forecast —
  the same refusal as `night_lab` dropping model output it cannot check.
- **No edge is inferred.** Connections come from declared `depends_on` and
  nothing else. An invented edge is a shortcut across the whole graph, which is
  the over-linking defect the graph auditor (now in `hermes-agent`) convicts in
  someone else's resolver.
- **Nothing that moves money is proposed as an action.** It is returned with
  `safe=False` and the reason, for a person. Same doctrine as
  `tools/ai_company.py`: agents move information, people move money. Asserted by
  a test, not trusted.

It also names what it **cannot see**. A domain with no adapter reads exactly
like a domain with nothing wrong, so `sources` and every brief list the blind
spots out loud.

## What triggers an interruption

Three, chosen by the owner on 2026-09-02, and no others:

- `stopped` — something quit working and will not restart on its own
- `money` — something commits funds
- `blocking` — a decision is waiting on a person and work is stalled behind it

A thresholds trigger was offered and **declined**. None of these three needs a
number picked out of the air, and false alarms are how alerting dies.

## The first slice, and the two that followed

The fleet and this repository were wired first. That was chosen because it is
the only domain whose live sources a cloud session can reach *and verify*, and
building where it could not be tested would have made the owner the test
harness.

`desk` and `content` followed on 2026-09-02, and the promise held — both dropped
in beside `observe_jobs` and the assembly did not change. Neither is *read* from
this process. The desk's feeds are on the owner's Windows machine and content's
credentials turned out to be live but held in **MCP connectors, which only a
Claude session can call** — so both are *carried* here as redacted signals
committed to git, by whoever can reach them. `docs/desk-content-adapters.md` has
the protocol, the schema that makes publishing them safe, and what each refuses;
the reasoning is in
[the decision entry](decisions/2026-09-02-a-blind-domain-is-carried-not-reached.md).

Five sources today:

- `observe_jobs` — failure streaks and live blockers, from `runs.jsonl`
- `observe_schedule` — jobs that should have reported and have not, from the
  `$jobs` table in `register_desk_agent.ps1` (parsed, never copied)
- `observe_git` — uncommitted work, and commits that never reached GitHub
- `observe_desk` — reports, the paper book, the journal export gap and the
  broker, from `signals/desk.json`
- `observe_content` — the market-close render and the publishing and analytics
  connectors, from `signals/content.json`, in two independently stamped halves
- `observe_business` — where each readiness engagement is stuck, and what in the
  company blueprint commits money

`business` landed on 2026-09-06 and closed the last unwired domain.

## The business domain, and why it is read rather than carried

`observe_business` reads two sources that fail independently:

- **the engagements** — for each folder under `engagements/`, the refusal
  `tools/engagement.py`'s `advance` would give if it were called right now.
- **the company blueprint** — the money gates `tools/ai_company.py` convicts in
  `docs/blueprint-one-person-ai-company.json`.

`desk` and `content` are *carried* here as signals because neither can be
reached from this process. Business is different: an engagement is JSON and
markdown in the checkout and the blueprint is committed, so both are read
directly. **The limit is worth stating rather than glossing:** `engagements/` is
gitignored — this fork is public and a client's process map is nobody's business
but theirs — so a **cloud session sees the blueprint and no engagement at all**,
and names that in its blind spots rather than reporting calm. A local session
sees everything. If cloud visibility is ever wanted here, the answer is the same
emitter pattern `desk_signal` and `content_signal` already use — but what may
safely leave a client's folder is the owner's call, not an implementation
detail, so it was not guessed at.

### Two triggers fire, and only two

An engagement parked on `approval` is a named person's decision with the
implementation plan and go-live behind it: that is `blocking`, stated exactly.
An AI step reaching a payment or contract tool with no person step in front of
it is `money`. Everything else the gates catch — a deliverable not written, a
deck not built — is `watch` with **no trigger at all**, because it is unwritten
*work* rather than an undecided *decision*, and a trigger that spreads to
everything that looks stuck is a trigger nobody reads. `stopped` fires nowhere
in this domain: nothing here runs unattended, and borrowing the fleet's word for
a situation it does not describe is how a vocabulary rots.

### What it refuses

- **No threshold, again.** How long an engagement has sat at its current phase
  is carried as the metric `days_at_this_phase` and never as a verdict. The
  owner declined a thresholds trigger when this layer was commissioned; the
  count is evidence for the session already reading the brief.
- **The day count stays out of the summary.** `changes` diffs summaries, so a
  number that ticks over at midnight would report every open engagement as
  changed every single day — and a delta that always fires carries no
  information. Pinned by
  `test_the_day_count_never_reaches_the_summary_so_nothing_changes_at_midnight`.
- **`unmarked_touch` is not reported.** `ai_company` calls it "a list to
  confirm, not a verdict" and the committed blueprint produces nine. Nine
  permanent info lines is how a brief stops being read. The two real warnings —
  `partial_gate`, `automation_commits` — are counted in one summary line rather
  than raised, so a regression still shows up in `changes` without costing an
  interruption.
- **One declared edge, no inferred ones.** A gate finding `depends_on` the
  blueprint it was read out of, because that document is why the finding
  exists. Two engagements stuck the same way share nothing.

### The gate is a prediction, and it is held to that

The adapter says what `advance` *would* refuse on. That is a claim about another
module's behaviour, and restating another module's rules is how a check goes
stale in silence. So the load-bearing test plants all five states, asserts the
adapter names each one, and then calls the **real** `advance` and asserts it
agrees — four refusals and one pass. A phase added to `PHASES` therefore cannot
leave the adapter describing a gate that no longer exists while the suite still
goes green.

The state worth knowing about is `deliverable-seeded`: `engagement.py` can seed
a target-design deliverable from the blueprint, and a seeded file **exists and
is not empty**, so every folder listing and every "is it written?" check reads
as done. Only the `UNEDITED REFERENCE` line separates it from real work.

## Four false alarms it produced on its first run

Every one looked plausible. They are pinned as named regression tests in
`tests/test_awareness.py`, because each would have quietly trained the owner to
stop reading the channel:

1. **`partial` counted as failure** — reported premarket's streak as 8 when the
   truth was 3. runlog's vocabulary is `(failed, partial, skipped, ok)` and a
   run that did some of its job is not a run that did nothing.
2. **A dormant job read as failing** — `alerts` was switched off on 2026-08-29;
   its last real run, a fortnight old, was reported as "its last run". `skipped`
   is transparent *between* failures, never at the head.
3. **A closed blocker convicted again** —
   `journal-path-outside-session-working-directory` killed five runs and the run
   log explicitly recorded it closed. Counting history alone re-convicted it. A
   blocker is live only if it appears in some job's *latest* record.
4. **A blocker on a switched-off job** — `no-alerts-configured-on-agent-login`
   is real, and blocks a job nobody runs.

Recurrence is still counted across the whole log, because "how long has this
been going on" is the part worth knowing. Only *liveness* reads the head.

## Reading the output

```bash
python tools/awareness.py brief           # the seven answers
python tools/awareness.py brief --short   # catch-up form, six lines at most
python tools/awareness.py brief --json
python tools/awareness.py record          # append this moment to the log
python tools/awareness.py sources         # what it sees, and what it cannot

python tools/desk_signal.py emit          # on the machine: refresh signals/desk.json
python tools/content_signal.py capture --platform-json -   # from a session with the connectors
```

`brief` exits 1 when something carries `act` severity and a trigger, so a
wrapper can react without parsing the text.

`record` is what makes "what is changing" answerable. Run it when you want a
mark in the sand; nothing runs it automatically, because a log that grows on
every session read would be measuring the reader.
