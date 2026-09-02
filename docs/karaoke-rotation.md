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

## The room is never silent

House music is assumed playing when the night opens. The engine emits
`HOUSE_OFF` the instant a singer starts and `HOUSE_ON` the instant the
stage goes bare with nobody mid-walk-up onto it -- and nothing between
back-to-back songs, so the music never flickers through a clean handover.
The simulator integrates every second the stage was empty while the room
wanted music and verdicts that none of it was uncovered: the gap time an
outro call cannot remove is carried by the house playlist instead.

## The no-brainer shell

`room.py` + `queue_server.py` + `static/karaoke-queue.html` wrap the
engine into the thing a pub actually touches:

    python -m tools.karaoke_server.queue_server

One command, one address (LAN only -- same hosting rule as the
leaderboard). For a machine outside this repo, the same OS ships as one
portable file: `build_standalone.py` concatenates the tested modules and
embeds the page verbatim into `karaoke_os.py` (stdlib only, Python
3.10+), and the release workflow freezes that into `KaraokeQueue.exe`
for a Windows machine with no Python. Both selfcheck before they ship,
and the artifact is generated, never hand-edited -- the tested modules
and the shipped file stay the same code. The big screen opens `/screen`: now singing, the draw
reveal with its walk-up countdown, a QR to join, an event ticker, and
YouTube playback when the song came in as a link -- the screen corrects
the engine's guessed duration from the real player (`retime`), including
"it just ended". Phones scan the QR: name, song (paste a link or just
type a title), done. A returning name is greeted with "your usual?" chips
from the songs it sang here before. The called phone becomes a full-screen
YOU'RE UP with the countdown and one button. The waiting list a poll
returns is alphabetical on purpose: the order is the secret, and the only
ordering that ever leaves the server is the call itself.

## The QR is drawn on the machine

The screen used to fetch qrcodejs from a CDN. That is a network dependency
on the one element that gets anybody into the queue, and a venue's Wi-Fi has
no reason to reach the internet. A captive portal is worse than no internet:
the `<script>` loads, is a login page rather than the library, and the call
throws. So the encoder is inlined in the page and ships inside
`karaoke_os.py` -- byte mode, error correction M, versions 1-6, no
dependency of any kind.

Two things prove it rather than assert it. `static/karaoke-qr.test.js`
compares the matrices module for module against fixtures produced by
python-qrcode and decoded back to their own URLs by OpenCV, an unrelated
implementation; reintroducing the format-bit bug that was found while
writing it fails 19 of those cases. And the page was loaded in a real
browser with every off-box request aborted, where it drew a QR that OpenCV
read back as exactly the address the server had declared.

**The address comes from the server, not from `location.origin`.** Only the
process knows which address phones can reach, so it states it in a
`karaoke-join` meta tag. A screen opened at localhost would otherwise
publish a QR that every phone in the room resolves to itself -- the whole
product silently broken, which is precisely the bug that shipped once
already. The page falls back to its own origin when no tag is present, and
says so on screen when that origin is a loopback address.

## Testing alone, and the host desk

Three things the owner hit running the queue alone on one PC, 2026-09-02.

**Solo calls.** With one singer in the room, queueing a song fired the draw
at once, with a walk-up countdown, and missing it twice produced "we called
you twice" from a room containing nobody else. Now a draw whose pool holds
exactly one singer makes a *solo* call: `Call.solo` is set, the deadline is
`math.inf`, and `_step` never fires a no-show for it. The phone's YOU'RE UP
shows "Only you in the draw so far — tap when you're ready" instead of the
clock; the screen says "get to the stage whenever you're ready". The moment
a second singer becomes eligible the call stays with the same singer but
converts, on the next tick, into an ordinary timed call with a fresh grace
(floored at the song's end during an outro); no new CALL event, because
nobody new was called. A room of two never produces a solo call, and the
poll carries the flag as `called.solo` / `you.solo` with `deadline_in_s`
`null` (never `Infinity`, which is not JSON). Note the outro case: a singer
called while the only other person is *on stage* is solo too — the stage
is not the draw.

**The host desk.** The manual promised desk sign-in; the page never had it.
`/screen` now has a small **Host** button in the bottom-right corner (a
click, not a hover, so a TV remote works) that opens a panel in the side
column: add a singer (name plus optional song or YouTube link, parsed by
the same `youtubeId` the phone uses), **skip the call** (shown only while
someone is called and not yet at the stage; `Rotation.skip_call` is the
no-show path, so it costs the same strike the clock would), and **end the
song** (shown only while someone is singing; `retime` with zero
remaining). Routes are `/api/host/add`, `/api/host/skip`, `/api/host/end`
and the page wires the panel only when its role is `screen` — the phone
never grows one, and `tests/test_karaoke_queue_room.py` reads the script to
prove it.

Two things about skip specifically. **The skipped singer sits out that one
draw** whenever anyone else is eligible — a strike, as above, but not the
mic handed straight back. Measured before the rule existed: in a seeded
room of two, the honest lottery returned the same name 32% of the time,
which is a correct rotation and a button that looks broken. They are back
in the pool for the draw after, and a room where they are the only one
left still calls them.

**And the panel is a convenience, not a permission.** Hiding it from the
phone role keeps it out of a singer's way; it does not stop anyone on the
Wi-Fi POSTing to `/api/host/skip` themselves. That is the same trust
model the rest of the room already runs on — `/api/retime` has always
been open too — and it is the right one for one room on one LAN for one
night. A venue that needs the desk to be the only desk needs a secret on
those three routes, which this does not have.

**A title, not a link.** A pasted YouTube link used to appear as the URL on
both the phone and the screen. `queue_server.py` — the edge, never the
engine or the room — now asks YouTube's oEmbed endpoint for the video's
title (2-second timeout, one fetch per video id for the life of the
process, misses remembered too so a room with no uplink pays the timeout
once per link, not once per poll). The fetch runs outside the room lock.
**It needs internet**; when anything goes wrong — no uplink, a captive
portal, a slow answer, a non-200, bad JSON — the raw link is kept exactly
as before, so a venue with no uplink behaves exactly as it did. The
fetcher is injectable (`build(title_lookup=...)`, `Handler.title_lookup`),
and the suite drives the real `do_POST` through a socketless handler with
a fake lookup and a `urlopen` that records any call, so no test ever
reaches YouTube.

## The address the QR publishes is a guess, so it shows its working

2026-09-02, on the owner's machine: the server printed
`http://10.5.0.2:8772` and no phone in the room could reach it. The old
`lan_address()` opened a UDP socket toward the internet and read back
which interface the OS chose — a good way to answer "how do I reach the
internet" and the wrong question entirely. A VPN was up, so the answer
was the tunnel.

It now collects **every** IPv4 the machine answers to (the default route
plus `getaddrinfo` on the hostname), ranks them by how likely a venue
handed them out — `192.168.*` first, then `172.16-31.*`, then `10.*`,
with loopback and a DHCP-less `169.254.*` last — and publishes the best.
The ranking puts `10.*` below the other private ranges precisely because
it is what VPN clients and container bridges help themselves to.

**And it prints the rest.** No rule gets this right on every machine, so
when there is more than one candidate the console lists the others with
`--host <address>` to pin one. A wrong guess costs a glance at the
screen rather than someone going to ask the operating system. Ranking is
pinned by `TestWhichAddressPhonesCanReach`, including the exact
`10.5.0.2` / `192.168.1.50` pair that produced the bug.

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
| no silent second | every stage gap is covered by house music, all nights |

The suite (`tests/test_karaoke_rotation.py`) pins the mechanism the same
way: a convict test where the pressure is planted and the rule must bite,
an acquit test where the room is clean and the rule must stay out of the
way, and a fixed-seed night whose numbers move only when behaviour moves.

## Multi-room

One `Rotation` is one room for one night, and rooms share nothing except
the profiles dict a caller chooses to pass to both. Isolation between
venues is therefore the default, not a feature to build.
