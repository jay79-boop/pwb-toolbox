# A blind domain is carried to the observer, not reached by it

*Decided 2026-09-02.*

## What was believed

`tools/awareness.py` shipped with two domains listed as permanently unseeable:

> the desk feeds live on the owner's Windows machine and content needs
> credentials that are not connected

Both were read as *waiting on something* — the desk waiting on a route to a
Windows disk, content waiting on an integration nobody had set up.

## What was actually true

**Content's credentials were already connected.** Called from a cloud session on
the day this was written: Blotato answers with an `active` `starter`
subscription and one connected TikTok account; Windsor.ai answers as the owner
on a Trial plan with `tiktok_organic` connected. The line in `docs/awareness.md`
was written before that and never revisited.

The blind spot had moved rather than closed, and the new shape is the
interesting one: the credentials live in **MCP connectors, which only a Claude
session can call**. The awareness core is Python. It will never call one.

**The desk's half was true and stays true.** No cloud session reaches
`C:\Users\Gexio\OneDrive\...`, and none ever will.

## The decision

**Stop trying to give the observer a route to the domain. Give the domain a
route to the observer.**

Both adapters read a file out of `signals/`, committed to git. The desk's is
written by `tools/desk_signal.py emit` on the machine, from `run_job.ps1`, and
carried by the push that already carries `runs.jsonl`. Content's is written by
`tools/content_signal.py capture` from whichever session holds the connectors.

This is not new. `runs.jsonl` is tracked precisely because git is the only thing
the machine and a cloud session share, and it is the only part of the desk agent
a cloud session can see at all. The decision is to treat that as the general
answer rather than one tool's workaround.

Three consequences followed, and each was a choice:

**Redaction by schema, not by discipline.** This fork is public and every other
desk directory — `spec_desk/`, `night_lab/`, `season/`, `engagements/` — is
gitignored for that reason. The signals are tracked, so every field is declared
and the only permitted values are numbers, booleans, ISO dates and closed
vocabularies. `validate()` raises before anything is written. There is no
free-text field to forget to scrub, which is a stronger guarantee than
remembering; the tests plant a ticker, two prices, a balance, an account number,
a handle and a caption, and assert none survive.

**The bridge is observed before the domain is.** A signal that stops being
written looks exactly like a desk with nothing wrong — the same confusion the
whole layer exists to resolve. So a stale signal is reported as the thing that
stopped, and the facts underneath it are labelled *then, not now*.

**Staleness is the trading calendar.** A signal is stale once a full NYSE session
has elapsed without one being written, counting days strictly between, so a
session still open is never counted and a weekend needs no exception. The
alternative was a number of hours, and a thresholds trigger was offered to the
owner on this layer's first day and deliberately declined.

## What was not done, and why

**No adapter reaches a live service from Python.** It would have needed a second
copy of credentials in `.env`, on a machine whose `.env` is already the thing
that must never be committed, to duplicate a connector that already works. The
session is the transport.

**The broker is tri-state.** `unknown` is not `disconnected`. It was the one desk
fact that could not be verified from the session that built it, so it was wired
to say nothing rather than to assume: no heartbeat, no claim, and the absence
goes to the blind-spot list where it can be read as a gap rather than a finding.

**`business` is still unwired** and still named out loud on every run.

## The thing that only showed up by looking

Blotato has never published anything — a query across all statuses since
2025-01-01 returns empty — and the two services are connected to two *different*
TikTok identities (`@jayshong6` on Blotato, `AlaskaM` on Windsor). A paid,
connected publishing connector that is not in the loop is exactly the state that
reads as healthy from every direction except the one that asks what it produced.
That is the case the content adapter exists to name.
