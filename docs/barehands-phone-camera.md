# barehands on this machine, with a phone for a camera

[barehands](https://github.com/jaredrhod/barehands) turns a webcam into a
hand-tracked interface: notes, images and 3D models float over the camera feed as
glass cards you pinch, throw and pull across the room. It is unrelated to the
trading library — nothing under `pwb_toolbox/` imports it, and it is not cloned
into this repository. It is written down here because the owner has **no webcam**,
and the phone-as-camera route is not covered by barehands' own docs at all: the
word "phone" appears zero times in that repository.

Everything under "Verified" was established by cloning the repo into a cloud
container and reading or running it. Everything under "Unverified" needs a phone,
a camera and a Windows desktop, and this session had none of the three.

## The question that gets asked first: can the phone just run the board?

**No, and two independent things stop it.** Both are worth knowing before anyone
spends an evening trying.

1. **The server binds to loopback only.** `server.py` ends on
   `ThreadingHTTPServer(("127.0.0.1", port), Handler)`. A phone on the same Wi-Fi
   cannot reach the page at all — the connection is refused before any of this
   matters.
2. **Even if it could, Chrome would refuse the camera.** `getUserMedia` needs a
   secure context, which means HTTPS or localhost. A LAN address over plain HTTP
   is neither. barehands' own source comments on this at the top of `server.py`:
   *"localhost = a secure context, which is what lets the browser open your
   camera."* The loopback bind is the feature, not an oversight.

Widening the bind would therefore also require TLS, and would put a board that
accepts POSTed commands on the local network. Don't.

## What does work: make the phone a webcam on the PC

`stage.html` does not care where the pixels come from. It calls
`enumerateDevices()`, filters to `videoinput`, and **tries every camera the OS
offers** until one opens. `C` cycles cameras live, the choice persists in
`localStorage`, and `?cam=<label substring>` pins one. Any virtual-camera driver
is indistinguishable from a real webcam to that code.

Two routes, both free:

| Route | Works on | Link | Latency |
| --- | --- | --- | --- |
| **Iriun Webcam** over a **USB cable** | Win 10 + 11, Android + iPhone | app on the phone, driver on the PC, from `iriun.com` | best — wired |
| Windows 11 **Connected camera** | Win 11 only, **Android only** | Settings → Bluetooth & devices → Mobile devices → Manage devices | wireless, so worse |

**Prefer Iriun over USB.** Not because the built-in route is bad, but because
latency is the one thing that decides whether the gestures feel alive, and a cable
beats Wi-Fi every time. The built-in route's advantage is that it installs nothing
on the PC; its cost is that it is Android-only, Windows-11-only, and wireless.
720p at 30fps is plenty — MediaPipe downsamples anyway, so the free tier is not a
limitation here.

## Windows traps in barehands itself

- **`run.bat`, never `python3 server.py`.** A clean Windows has a Microsoft Store
  stub named `python` that `where` finds happily and that exits 9009 when run.
  `run.bat` handles this properly: it *executes* each candidate interpreter rather
  than merely locating one. The README says so; it is repeated here because the
  copy-pasted macOS command is the obvious thing to reach for.
- **`bin/board.sh` and `bin/board-state.sh` are bash, and there is no `.bat`
  twin.** They also call real `curl`, which on PowerShell is an alias for
  `Invoke-WebRequest` and takes entirely different arguments. Run them under Git
  Bash, or skip them: they are thin wrappers around
  `POST http://127.0.0.1:8794/cmd` with a JSON body, which
  `Invoke-RestMethod` does natively. The server enforces its own action allowlist
  (`add_img`, `add_card`, `clear`, `reset`, `hand`, `give`, `present`, …) and a
  media jail, so posting directly loses no safety.
- **Clone it outside OneDrive.** `state/state` is a live runtime file rewritten on
  every session-state change, so a synced folder would churn on it continuously —
  and any git repo under OneDrive hits the `gc --auto` lock prompt that
  `docs/local-checkout.md` describes, which a `git pull` can walk straight into.
  `C:\Users\Gexio\barehands` is the right home. `barehands.json`, `state/*` and
  the media folders are all gitignored, so updates never touch personal config.

## The install is agent-driven by design

barehands ships `barehands.md`, a six-phase setup script written to be *read by a
Claude Code agent*, not by a human: it proves the server runs, interviews the
owner, writes `barehands.json`, wires the ring into Claude Code's hooks, and
leaves a desktop launcher. The intended install is one sentence pasted into a
local session. That is the right route here — it is the no-code path, and a cloud
session cannot do any of it.

One caveat for that agent: **the ring's hooks want an edit to `settings.json`**,
which sits behind the destructive-action gate on this machine. Expect to approve
it, and see the `gexio-machine` skill's note that a CLI flag on the launcher often
achieves what a guarded settings edit would.

## Unverified

No camera, no phone, no Windows desktop was available to the session that wrote
this. Specifically untested:

- **Whether the gestures survive the added latency of a phone bridge.** The quick
  pinch ("tap") is the gesture most likely to feel mushy; the claw, which wants a
  deliberate two-second strain, is the most likely to survive. If tracking is
  poor, `?res=1280x720` on the tracker URL is the documented lever for slow
  machines, and `TROUBLESHOOTING.md` ships a debug overlay and pose sampler for
  refitting the gates to a specific hand.
- **Iriun's current installer and the Connected-camera menu path**, both taken
  from vendor and Microsoft documentation rather than from a running machine.

What *was* run: the server booted on stdlib Python 3.11 in a Linux container and
served `stage.html` at HTTP 200, 157,894 bytes. That confirms the dependency story
— standard library only, no build step — and nothing about the camera.
