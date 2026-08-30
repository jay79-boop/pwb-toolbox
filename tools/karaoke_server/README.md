# Shared karaoke leaderboard

A small server so several people can post scores to one board. It serves
`static/karaoke-box.html` and the score API from the same origin, so a room
full of phones pointed at one host share a leaderboard.

Standard library only — no new dependencies, nothing to install.

Unrelated to the trading library; it exists purely to give the karaoke page
somewhere to post scores.

## This has to run on a machine that is actually on your Wi-Fi

"Open that address on any device on the same network" only works if the
device running `python -m tools.karaoke_server` is on that network. A Claude
Code **cloud** session (claude.ai/code) is a container with no route to your
router, so it cannot start this in a way your phones could ever reach — the
process would come up, but only the sandbox itself could open the port.
Start it from a **local** Claude Code session (running directly on your
machine) or by hand, not from the cloud. See the `gexio-machine` skill's
"Which machine are you on?" section if it is unclear which kind of session
you are talking to.

## Run it

```bash
python -m tools.karaoke_server
```

```
Karaoke board on http://localhost:8770  (scores in karaoke-scores.json)
Open that address on any device on the same network to share a board.
```

Open that address, turn on **Score me**, and sing. Anyone else on the same
network who opens the same address lands on the same board — the page
notices it was served by a board host and connects on its own.

Options:

```bash
python -m tools.karaoke_server --port 9000 --db /var/lib/karaoke.json
python -m tools.karaoke_server --host 127.0.0.1        # this machine only
python -m tools.karaoke_server --origin https://example.com
```

`--db` also reads from `KARAOKE_DB`. Set `KARAOKE_QUIET=1` to silence the
request log.

## Pointing a page somewhere else

A copy of the page opened straight from disk makes no requests at all. To
attach it to a server, paste the address into **Shared board** in the
leaderboard panel and press Connect. Blank means scores stay in that
browser.

Cross-origin requests are allowed (`--origin` sets the header, `*` by
default), so a page hosted elsewhere can post to a central server.

## API

| | |
|---|---|
| `GET /` | the karaoke page, wrapped as a document and told where the board is |
| `GET /api/scores?limit=20` | `{"scores": [...]}`, best first |
| `POST /api/scores` | one run as JSON; returns `{"entry": {...}, "scores": [...]}` |
| `GET /healthz` | `{"ok": true}` |

A run looks like:

```json
{
  "score": 87, "title": "Twinkle, Twinkle, Little Star", "name": "Ada",
  "code": "001", "rank": "Showstopper", "notes": 42, "hit": 37,
  "tempo": 100, "duet": false, "part": "a"
}
```

Only `score` (0–100) and `title` are required. Text is length-capped and
stripped of control characters, numbers are range-checked, and `at` is
assigned by the server — a client cannot backdate itself to win a tie.
Malformed runs get a 400 with a reason. The file keeps the best 500 runs.

## What this is not

There is no authentication. Anyone who can reach the port can post a score
or read the board, and nothing stops someone submitting a 100 they did not
sing. That is the right trade for a party on a home network and the wrong
one for the open internet — don't expose it publicly without putting
something in front of it.

Scores live in one JSON file. Back it up by copying it.

## The random rotation (who sings next)

`rotation.py` runs the queue for a room: sign in with a song attached, and
a weighted random draw picks each next singer — nobody knows who is up
until the call goes out, ~a minute before the current song ends. Fairness
rails: a wait ceiling, a just-sang cooldown, fewest-songs-tonight odds, a
newcomer boost, and strike-out/come-back handling for no-shows. Walk-up
times are learned per singer and stretch the call lead as the room fills.

`sim.py` proves it on synthetic pub nights and judges itself:

```bash
python -m tools.karaoke_server.sim report
```

To actually run a night (same LAN rule as the board -- a machine on the
venue's Wi-Fi, never a cloud session):

```bash
python -m tools.karaoke_server.queue_server
```

Open `/screen` on the address it prints for the stage display (now
singing, the draw reveal, a QR to join -- drawn on the machine, with no
CDN and no internet -- YouTube playback for link songs,
house music state); phones scan the QR and get the three-tap flow: name,
song, and a full-screen YOU'RE UP when the draw lands on them. Singer
memory lives in `karaoke-profiles.json` next to where you ran it.

`docs/karaoke-rotation.md` is the protocol — including what the rotation
refuses to promise (a flat "never wait more than 4 draws" is impossible in
a 15-deep queue; nobody can read your YouTube history, so it remembers
what you sang *here* instead).

## Take it to another computer

The repo is the workshop, not the product. Two portable builds exist:

- **`karaoke_os.py`** — the whole OS (engine, server, page embedded) as one
  stdlib-only file. Any machine with Python 3.10+: copy it, `python
  karaoke_os.py`, done. Build it locally with
  `python tools/karaoke_server/build_standalone.py`.
- **`KaraokeQueue.exe`** — the same file frozen with PyInstaller for a
  Windows machine with no Python at all. Double-click; the console shows
  the address and the big screen opens itself.

`.github/workflows/release-karaoke.yml` builds and selfchecks both on
every manual run (grab them from the run's artifacts), and a tag push
`karaoke-v*` attaches them to a **draft** GitHub release for the owner to
publish. The single file is generated -- edit the modules here, never the
artifact; `tests/test_karaoke_standalone.py` proves the build runs a real
night and carries the page byte for byte.
