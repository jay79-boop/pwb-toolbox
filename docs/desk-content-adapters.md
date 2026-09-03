# The desk and content adapters

`tools/awareness.py` shipped with the fleet and this repository wired, and the
desk, the businesses and content listed under *not visible from here*. The
reason given was:

> the desk feeds live on the owner's Windows machine and content needs
> credentials that are not connected

Half of that was still true on 2026-09-02 and half of it was not.

## What was checked, and what it found

**Content's credentials are connected.** Both were called from a cloud session:

| service | state | connected |
|---|---|---|
| Blotato | `active`, plan `starter` | 1 account: TikTok |
| Windsor.ai | authenticated, `is_paid: false` (Trial) | 1 connector: `tiktok_organic` |

So the blind spot moved rather than closed. The credentials are live, but they
live in **MCP connectors, which only a Claude session can call** — no Python
process in this repository can reach one, and the awareness core is Python.

Two things fell out of that check and are worth writing down, because neither is
visible from the credentials alone. The two services are connected to **different
TikTok identities** — Blotato holds `@jayshong6`, Windsor holds an account named
`AlaskaM` — so they are not two views of one channel. And Blotato has **never
published anything**: a query across all statuses since 2025-01-01 returns an
empty list. The publishing connector is paid for, connected, and not in the loop;
`tools/market_close`'s daily process ends with a human pasting into the platform.

**The desk really is machine-bound**, and there is no route by which a cloud
session reaches `C:\Users\Gexio\OneDrive\...`. That half of the claim stands.

## The shape both adapters take

Neither domain is *read* from the awareness process. Each is **carried** to it,
by whoever can reach it, as a redacted signal committed to git.

```
the machine                     git                     any session
-----------                     ---                     -----------
desk_signal.py emit    ->   signals/desk.json     ->   observe_desk
   (run_job.ps1, after            (pushed by             (awareness.py)
    every desk agent run)          the launcher)

a session with the connectors
-----------------------------
content_signal.py capture ->  signals/content.json ->  observe_content
```

This is not a new idea here. `tools/desk_agent/runs.jsonl` is committed on
purpose and pushed by the launcher for exactly this reason, and it is the only
part of the desk agent a cloud session can see. The signals extend that from one
tool's log to two domains.

## Why publishing them to a public fork is safe

Not because anyone remembered to leave the ticker out.

Every field is declared in a `SCHEMA`, and the only values it permits are
integers, floats, booleans, ISO dates, and words from a closed vocabulary.
`validate()` raises `Unpublishable` **before a byte is written**. There is no
free-text field, so there is nowhere for a symbol, a price, a balance, an account
number, a caption or a filename to go.

`tests/test_desk_signal.py::test_nothing_a_paper_book_holds_survives_into_the_signal`
plants a ticker, two prices, an account balance and a broker account number in
the input and asserts none of them reach the output. The content half does the
same with a handle, an account name and a caption.

The guard **refuses rather than scrubs**. A value that does not fit the schema is
a bug in the emitter, and quietly dropping it would hide that bug behind a file
that looks fine.

## Staleness is the trading calendar, never a number of hours

A bridge that stops being written looks exactly like a calm desk. That is the
same confusion the awareness layer exists to resolve, one level up, so it gets
the same treatment: **the bridge is observed before the desk is**, and a stale
signal is reported as the thing that stopped, with the desk facts underneath it
explicitly described as *then, not now*.

The rule is `sessions_missed()`: a signal is stale once a full NYSE session has
elapsed without one being written. Days strictly between the two, so a session
still open is never counted — "today has not finished" is not evidence that
anything stopped. Weekends and holidays fall out of the calendar for free, which
is why this is a rule and not a threshold in hours. A Friday signal read on
Monday is current; the same signal read on Tuesday is not.

The same rule does four other jobs: how far behind the paper book is, whether the
broker heartbeat is live, whether a market-close render was produced, and — via
`desk_watch`'s calendar — which sessions left no report at all.

## What each adapter refuses

Following the four refusals `docs/awareness.md` already carries:

- **A missing signal is a blind spot, never a quiet domain.** `observe_desk(None)`
  and `observe_content(None)` return *nothing*, and `collect` names the absence
  by file path and by the command that fixes it.
- **A signal that read nothing is a broken emitter, not a quiet desk.** Found by
  running `emit` in a checkout with no desk in it: the bridge reported "the desk
  signal is current" and stopped, because the stamp was fresh and every field was
  empty. That is exactly the reading this layer exists to prevent — an emitter
  pointed at the wrong machine (a second checkout, a OneDrive folder that has not
  synced, a task registered without the credential that reaches the disk) looks
  identical to a desk with nothing wrong. `reads_nothing()` convicts it.
- **Each half is only as current as its own stamp.** The content platform half
  gets the same calendar rule as the desk bridge: a capture from a fortnight ago
  reporting no posts is a fact about a fortnight ago, and it is labelled *then,
  not now* in front of the facts it qualifies.
- **`None` is not zero.** Every numeric field is `None` when the input could not
  be read. The rule that fires on "there is no export route" (`False`) does not
  fire on "nobody could tell" (`None`).
- **The broker is tri-state.** `unknown` is not a synonym for `disconnected` —
  one says the desk is unplugged, the other says nobody looked. `unknown`
  produces no observation at all and goes to the blind-spot list. This is the one
  desk fact that could not be verified while it was built, so it was wired to
  refuse rather than to assume.
- **The content halves never vouch for each other.** The render half is machine
  facts; the platform half is an MCP read. Each carries its own timestamp, and a
  fresh capture of one is never allowed to make the other look current — or
  stale. A capture with no platform half produces no publishing observation at
  any severity.
- **The journal's age is reported and never judged.** It is a document the owner
  writes when there is something to write, so no elapsed-time rule could tell a
  quiet fortnight from a broken one. What *is* judged is the export gap, which is
  a rule and not a threshold: closed trades exist in a machine-readable register
  and there is no route by which they reach the journal. That is `blocking` — a
  decision waiting on a person, from the 2026-09-01 desk agent run that found 18
  closed positions nothing was pointed at.
- **An unpaid analytics trial is watched and never interrupts.** Windsor.ai
  reports `is_paid: false`. A lapsed trial does not fail loudly; the reads simply
  stop, and a channel nobody watched looks identical to a channel with nothing
  happening. Worth seeing, not worth waking anyone.
- **Nothing moves money.** No observation in either adapter carries the `money`
  trigger, because neither domain has an action that commits funds. Same doctrine
  as `tools/ai_company.py`.

## Running them

```bash
# On the owner's machine. run_job.ps1 already does this after every desk agent
# run, and the same push that carries runs.jsonl carries the signal.
python tools/desk_signal.py emit
python tools/desk_signal.py emit --dry-run      # print it, write nothing
python tools/desk_signal.py show                # what the committed signal says, and its age

# From any session holding the Blotato and Windsor.ai connectors. The session
# reads the connectors, this reduces and validates what it read.
echo '{"blotato": {"subscription": "active", "accounts": 1, "posts_7d": 0},
       "windsor":  {"is_paid": false, "connectors": 1, "accounts": 1}}' \
  | python tools/content_signal.py capture --platform-json -

python tools/content_signal.py capture                  # render half only
python tools/content_signal.py capture --keep-platform  # render half, prior platform half kept with its own stamp
python tools/awareness.py sources                       # which halves are seen, and which are not
```

`emit` exits 0 always; `show` exits 1 when a session has elapsed since the signal
was written, so a wrapper can react without parsing the text.

## What is still blind

`business`. There is no adapter, and `collect` says so by name on every run.
