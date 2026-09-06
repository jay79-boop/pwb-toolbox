# The business adapter fires two triggers, and declines a threshold

*Decided 2026-09-06.* Completes the domain set opened by
[The awareness layer stores observations, not state](2026-09-02-the-awareness-layer-stores-observations-not-state.md)
and continued by
[A blind domain is carried, not reached](2026-09-02-a-blind-domain-is-carried-not-reached.md).

**Decision:** wire the last domain. `observe_business` reads the engagement
tracker's phase gates and the money gates `tools/ai_company.py` convicts in the
company blueprint. It raises `blocking` for an engagement parked on approval and
`money` for an ungated AI commit, and **nothing else carries a trigger at all**.

## Why this domain

The owner was offered three and picked this one, on the trade-off that the desk
was the highest-stakes but its live feeds are on his Windows machine. That
framing was drawn from a briefing that turned out to be **three days stale**:
`desk` and `content` had already been wired on 2026-09-03 by the carried-signal
route, and content's credentials — described as "not connected" — were live.
The choice still landed on the only domain left unwired, which is worth
recording precisely because it was luck rather than judgement.

The lesson is not about this adapter. **A stale briefing reads exactly like a
current one**, which is the same failure mode the whole layer exists for, one
level up. It was caught by reading the Action Ledger before opening the pull
request — the rule from
[one paste, two sessions](2026-09-02-one-paste-two-sessions-and-the-ledger-caught-it-late.md) —
and by nothing else. A clone taken minutes earlier had served a four-day-old
`main` without saying so, and every local check agreed with it.

## Read, not carried — and the limit that puts on it

`desk` and `content` are carried here as redacted signals because no process in
this repository can reach them. Business is different and is read directly: an
engagement is JSON and markdown in the checkout, and the blueprint is committed.

The limit is stated rather than glossed. `engagements/` is gitignored — this
fork is public and a client's process map is nobody's business but theirs — so a
**cloud session sees the blueprint and no engagement at all**. A local session
sees everything. If cloud visibility is wanted later, the answer already exists
in `docs/desk-content-adapters.md`: an emitter and a redacted signal. It was not
built, because what may safely leave a client's folder is the owner's decision
and guessing at it is how a public fork leaks something.

What *is* verifiable from anywhere is the adapter's behaviour: the tests build
real engagements through the real `advance` and sweep every gate. The behaviour
is provable here; only the owner's data is not.

## Two triggers, and the ones deliberately not fired

- An engagement at the `approval` phase is `blocking` by definition — the
  tracker refuses to move without a named approver, and the implementation plan
  and go-live sit behind it. That is "a decision is waiting on a person and work
  is stalled behind it", word for word.
- An AI step reaching a payment or contract tool with no person step in front of
  it is `money`, and `safest_actions` routes it to a person untouched.

Everything else the gates catch — a deliverable not written, a stakeholder deck
not built — is `watch` with **no trigger**. Not because these do not matter, but
because they are unwritten *work*, not an undecided *decision*, and a `blocking`
trigger that spreads to everything that looks stuck stops meaning anything. The
four false alarms on this layer's opening run were each of that shape:
plausible, and each would have quietly trained him to stop reading the channel.

`stopped` fires nowhere here, because nothing in this domain runs unattended.
Borrowing the fleet's word for a situation it does not describe is how a
vocabulary rots.

## The threshold that was declined, again

The obvious feature is "flag an engagement that has sat at one phase for N
days". It was not built. A thresholds trigger was offered when this layer was
commissioned and the owner declined it; reintroducing one inside an adapter
would honour the letter of that and not the point.

What ships instead is the metric `days_at_this_phase` on every open engagement —
attached to the observation, for the Claude session already reading the brief.
Same division of labour that keeps the whole layer from concluding.

One consequence is not obvious and is pinned by a test: **the count must stay
out of the `summary` string.** `changes` diffs summaries, so a number that ticks
over at midnight would report every open engagement as changed every single day.
A delta that always fires carries no information.

## Nine findings that are not reported

`ai_company.audit_gates` returns `unmarked_touch` for a step that reaches a
money tool without being marked as committing. Its own docstring calls this "a
list to confirm, not a verdict", and the committed blueprint produces **nine**.

Reporting them would have added nine permanent `info` lines to every brief,
forever, about a document that changes monthly at most. They are dropped. The
two real warnings — `partial_gate` and `automation_commits`, one and two in the
blueprint today — are counted into a single summary line rather than raised, so
a regression still surfaces through `changes` without costing an interruption.

## The gate is a prediction, and it is held to one

`observe_business` says what `engagement.advance` *would* refuse on. That is a
claim about another module's behaviour, and restating another module's rules is
how a check goes stale in silence — the failure
[a check that hardcodes its input is not a check](2026-08-29-a-check-that-hardcodes-its-input-is-not-a-check.md)
records.

So the load-bearing test plants all five states, asserts the adapter names each
one, and then calls the **real** `advance` and asserts it agrees: four refusals
and one pass. A phase added to `PHASES` cannot leave the adapter describing a
gate that no longer exists while the suite still goes green.

Eight mutations were applied to the adapter and each was caught by the test
written for it. Deleting the `SEED_MARKER` check failed both its own test *and*
the agreement test, which is the pair working as intended.

## What did not change

`assemble`, `connections`, `changes`, `project`, `attention` and
`safest_actions` are untouched, for the third adapter running. The schema was
designed to be domain-agnostic and has now been tested by three domains that
arrive by two different routes.
