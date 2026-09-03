# The usage panel cannot see the sessions that drained the window

*Decided 2026-09-03.*

A five-hour window went from fresh to 100% and the owner watched it happen in
minutes. They pulled the desktop app's "What's using your limits?" panel, which
reported `/finviz` 23%, an unnamed MCP 11%, `/gexio-machine` 5%,
`ccd_session_mgmt` 3%, and "14% ran above 150k context", against a session line
reading `174 in / 479 out / 11.5M cache read / 774.6k cache write` for $4.52.

None of that was the drain. The panel says so itself, in the line above the
table: **"this machine only, excludes claude.ai"**.

## Every session that spent anything ran in the cloud

`list_sessions` returned 40 sessions; 19 carried usage. All 19 report
`environment_kind: anthropic_cloud`. Sixteen of them report
`origin: desktop_app` — opened *from* the desktop app, executed on claude.ai.
The panel's own scope excludes exactly those.

So the percentages the owner was reading are shares of a slice that is not
where the window went. `/finviz` at 23% is 23% of the local remainder. It is a
true number answering a question nobody asked.

| | cache read | meter |
| --- | ---: | ---: |
| 19 cloud sessions | 354,128,848 | $434.02 |
| the one session in the panel | 11.5M (3.3M cloud-side) | $4.52 |

Counted twice — once through a JSON parser, once with a regex sum over the raw
response — both give **354,128,848** cache-read tokens and **1,692,134** output
tokens. Cache read is **94.44%** of every token moved. Output is 0.45%.

## One session was 38% of it

`Amplitude Analytics installation`: created 2026-09-02T06:34:31Z, last touched
2026-09-03T07:26:58Z — **alive 25 hours** — and in that time it moved

    cache read   133,021,694
    cache write    8,075,119
    input             26,386
    output           482,983
    meter            $166.86

That is 275 cache-read tokens for every token it produced. The repository's own
`session-size.sh` warns at 10M, 25M and 50M cache reads; this session passed the
top tier 2.7 times over and nothing fired, because that hook is scoped to
pwb-toolbox and this session was not in pwb-toolbox.

## It is not only cache read, and the second half is worse per token

Modelling the meter from published rates reproduces it closely enough to trust
the split — 98.9% at Opus 5 rates ($5 in / $25 out / $0.50 cache read / $10
cache write), 92.0% at Fable 5.1 rates ($10 / $50 / $0.25 / $12.50). Both
bracket the same conclusion, which is why the split survives the model
ambiguity described below:

| | tokens | share of tokens | share of spend |
| --- | ---: | ---: | ---: |
| cache read | 354.1M | 94.4% | ~21% to ~46% |
| cache write | 18.6M | 5.0% | ~40% to ~61% |
| output | 1.7M | 0.5% | ~14% to ~17% |
| fresh input | 0.6M | 0.2% | under 2% |

Cache read is the volume; cache write is the money. **Together they are 86% of
the spend and 99.4% of the tokens**, and they have one shared cause: a
conversation that is re-read in full on every turn and re-checkpointed as it
grows. Neither number is about the work being done. They are the cost of
remembering.

## Model choice was NOT the story — a first pass said it was, and was wrong

The session list reports `configured_model`, and six of the nineteen sessions
carried `claude-fable-5-1` on that field — $10/$50 per MTok, double Opus 5.
Attributing 65% of the meter to model choice on that basis was wrong, and the
archive responses are what disproved it. They also carry `last_served_model`
and `user_switched_model`:

| session | configured | last served | switched |
| --- | --- | --- | --- |
| Amplitude Analytics installation | fable-5-1 | **opus-5** | opus-5 |
| NVIDIA Cosmos installation | fable-5-1 | **opus-5** | opus-5 |
| NVIDIA skills installation | fable-5-1 | **opus-5** | opus-5 |
| Computer shutdowns investigation | fable-5-1 | fable-5-1 | opus-5 |
| Desk agent: push the run log | fable-5-1 | fable-5-1 | — |

Most were created as Fable and switched to Opus 5; only one ran Fable
throughout. Cost cannot arbitrate either — on the 133M session, Fable rates
model the meter to 95.1% and Opus 5 rates to 95.6%. The two are
indistinguishable from spend alone.

**`configured_model` is the model a session was created with, not the model
that served its turns.** Ranking spend by it produces a confident, wrong
answer, in the same way the desktop panel does. Read `last_served_model`.

So the correction is not a smaller version of the claim; it is the opposite
one. **Session length is the whole story, and there is no second factor.**

## The session that hit the wall, and what it was carrying

`Amplitude Analytics installation` is not merely the largest. Its record says
so directly:

    rate_limit_info: {"rateLimitType": "five_hour", "status": "rejected",
                      "isUsingOverage": false}
    post_turn_summary: "You've hit your session limit · resets 12:10pm (UTC)"
    context_usage:     {"max_tokens": 1000000, "used_tokens": 736272}

**736,272 tokens of live context.** That is what every one of its turns
re-read before doing anything at all, and it is the number the whole drain
reduces to. A 1M context window is not a budget to fill; filling it makes each
subsequent turn cost three-quarters of a million tokens to take.

`status: "rejected"` marks it as the session that actually met the wall.

## What follows

1. **The desktop usage panel is not the instrument for this.** It is scoped to
   one machine and silently excludes cloud sessions, which is where this
   account's spend lives. Diagnose with `list_sessions` and read
   `usage.cache_read_tokens`, or the panel will confidently rank the wrong
   things.
2. **Session age is the cost driver, not session count.** Cost per turn grows
   with everything said before it. A 25-hour session is not one session; it is
   a session paid for hundreds of times.
3. **Finish a task, then start a new session.** Resuming a large old session to
   ask one small question pays the whole history to ask it.
4. **Rank spend by `last_served_model`, never `configured_model`.** The first
   pass at this document blamed model choice for 65% of the meter using the
   configured field. Most of those sessions had been switched to Opus 5 and
   were served by it. A field that looks authoritative and answers a slightly
   different question is the same failure as the desktop panel, one layer in.
5. **A warning scoped to one repository warns about the wrong sessions.**
   `session-size.sh` did its job and was in the wrong place;
   `tools/install_spend_hook.py` puts a copy in `~/.claude/` so it fires
   everywhere, and it has to be run on the machine the sessions run on.

## What this does not claim

How the five-hour limit weights a cache-read token against an output token is
not published, and nothing here derives it. The `cost_usd` field is an
API-equivalent meter, not an invoice — confirmed with the owner on 2026-08-24,
when every session in that window reported `isUsingOverage: false` and nothing
was billed. Read the dollar figures as "how much of the window this consumed",
denominated in dollars because that is the unit the field happens to use.

Prior entry, different mechanism, same bill:
`docs/token-drain-2026-08-24.md` — self-re-arming Routines bound to persistent
sessions. That one was about *waking* an old session. This one is about *never
closing* it.
