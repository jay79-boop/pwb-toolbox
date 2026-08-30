# The random rotation: protocol

`tools/karaoke_server/rotation.py` is the queue "operating system" for a
karaoke room: people sign in with a song already attached (QR code on the
table, their phone, or the desk), and the rotation calls singers in a
weighted random order. Nobody knows who is next — that is the product — but
the randomness is bounded so it can never starve anyone or hand the mic to
the same voice all night.

`tools/karaoke_server/sim.py` is the measurement half: synthetic pub nights
(eager regulars, two-song casuals, one-and-dones, flaky no-shows,
latecomers) run through whole evenings, with PASS/FAIL verdicts.

    python -m tools.karaoke_server.sim report

## What the draw enforces

- **Ceiling.** Every draw you lose raises your odds, and past the ceiling
  you are simply taken next; among over-ceiling singers the longest waiter
  goes first, and no lottery win can jump them.
- **Cooldown.** Just sang: you sit out the next draws. Waived when nobody
  else is queued, so a one-person room still works.
- **Fewest songs first.** Each song already sung tonight multiplies your
  odds by `fewest_factor` (0.35), so a first-timer outranks a fourth-timer.
- **Newcomer boost.** A late arrival starts with better odds than their
  empty history would give them.
- **Strikes.** Miss your call and your priority climb restarts; miss
  `max_no_shows` calls and you are timed out (AWAY) until you tell the desk
  you are back. Away long enough and the rotation concludes you left. The
  night forgives a returner; the profile remembers the truth.

## The call goes out during the outro

The next singer is called `lead` seconds before the current song ends, so
the walk-up overlaps the outro. The lead adapts: observed walk-up times
(an EMA, remembered per singer across nights) times a safety factor, plus a
per-head allowance for how crowded the room is, clamped to 20–150s. A
called singer always has until at least the song's actual end, so an outro
call never shrinks anyone's grace. Measured against the same nights with
the lead forced to zero, the outro draw cuts dead air roughly fivefold
(~9 minutes a night saved in the standard mixed room).

## What it refuses to claim

- **"Nobody waits more than N draws" is impossible in a deep queue.** One
  winner per draw means with 15 people queued the *average* wait is 14
  draws. The simulator convicted the first build of exactly this promise.
  So the flat `max_misses` (default 4) rules only small rooms; past that
  the promise becomes relative — never wait more than `ceiling_ratio`
  (1.5×) a perfectly even rotation. Under full saturation the ceiling
  drives most calls and the rotation degrades toward round-robin; that is
  the promise holding, not the lottery failing. The cosmetic-randomness
  check therefore runs on a room with headroom, where the ceiling resolves
  ~0% of draws.
- **It does not read anyone's YouTube history.** The API for a viewer's
  watch history was retired by YouTube years ago; no third-party system
  can fetch it. What the rotation *can* remember is every song a singer
  performed **on this system** — the per-singer profile keeps the last 50 —
  which becomes the "your usual?" quick-pick. Song sources are otherwise
  opaque to the engine: a pasted link, a search result, a songbook id, or
  a bare title all queue the same way, so a venue that already has a
  karaoke rig can run title-only.
- **Profiles are keyed by name.** Two Daves in the same pub share a
  memory. Fine for a party; a multi-venue build needs a real identity.
- **Bad input is refused, not repaired.** A 4-second or 40-minute "song",
  a blank name, an unknown source: rejected with a reason, never clamped
  into something plausible.

## The verdicts

| verdict | planted / measured |
|---|---|
| waits stay bounded | worst misses at a call within ratio × fair share + backlog, every night |
| nobody starved | every reliable singer who queued and stayed sang at least once |
| no mic hog | identical eager singers finish within 2 songs of each other |
| outro draw beats stage-free draw | dead air vs the lead-zero control, same seeds |
| the lottery does the work | with headroom the ceiling resolves <10% of draws |

The suite (`tests/test_karaoke_rotation.py`) pins the mechanism the same
way: a convict test where the pressure is planted and the rule must bite,
an acquit test where the room is clean and the rule must stay out of the
way, and a fixed-seed night whose numbers move only when behaviour moves.

## Multi-room

One `Rotation` is one room for one night, and rooms share nothing except
the profiles dict a caller chooses to pass to both. Isolation between
venues is therefore the default, not a feature to build.
