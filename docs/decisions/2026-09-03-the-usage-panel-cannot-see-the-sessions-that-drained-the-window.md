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
the split — 98.9% against the Opus 5 sessions ($5 in / $25 out / $0.50 cache
read / $10 cache write), 92.0% against the Fable 5.1 ones ($10 / $50 / $0.25 /
$12.50):

| | tokens | share of tokens | share of spend |
| --- | ---: | ---: | ---: |
| cache read | 354.1M | 94.4% | ~21% (Fable) / ~46% (Opus 5) |
| cache write | 18.6M | 5.0% | ~61% (Fable) / ~40% (Opus 5) |
| output | 1.7M | 0.5% | ~17% / ~14% |
| fresh input | 0.6M | 0.2% | ~2% / ~0.2% |

Cache read is the volume; cache write is the money. **Together they are 86% of
the spend and 99.4% of the tokens**, and they have one shared cause: a
conversation that is re-read in full on every turn and re-checkpointed as it
grows. Neither number is about the work being done. They are the cost of
remembering.

## Model choice doubled it

Six of the nineteen sessions ran `claude-fable-5-1` at $10/$50 per MTok —
double Opus 5. Those six are **32% of the sessions and 65% of the meter**
($282.11 of $434.02), the 133M session among them. Nothing about installing
analytics or pushing a run log needed the most expensive model on the account.

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
4. **Fable 5.1 is a deliberate choice, not a default.** At 2x Opus 5 on input
   and output it needs a reason. Installation, configuration and log-pushing
   are not reasons.
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
