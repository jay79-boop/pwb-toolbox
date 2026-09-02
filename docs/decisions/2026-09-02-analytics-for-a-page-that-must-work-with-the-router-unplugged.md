# Analytics for a page that must work with the router unplugged

*Decided 2026-09-02.*

Amplitude was set up for this fork today: browser analytics on the karaoke
queue page, and Agent Analytics on the NVIDIA vision client. Both were
decisions the owner made from a box of options, and two of the options they
were offered were turned down for reasons worth keeping.

## No npm client, so the wizard's install step could not run as written

Amplitude's setup flow assumes `npm install @amplitude/unified` and a bundled
`import`. This repository has no `package.json` and no bundler; every browser
page here is one file that opens from `file://` with no build step, by design.
The three routes were: add a Node build step for one page, load the SDK from
Amplitude's CDN, or skip browser analytics as the wizard's own rule allows for
a project with no JavaScript client. The owner chose the karaoke queue page —
the one page here with users who are not the owner — and the mechanism was
ours to pick.

## Vendored and served from the LAN, never a CDN

The page already explains, at its QR encoder, why it fetches nothing at
render time: on a pub's captive portal a CDN `<script>` "loads" a login page
rather than the library, and `tests/test_karaoke_qr.py` pins the page's whole
network surface to fonts and the YouTube player. A CDN tag would have failed
that test and re-opened the hole the QR note closed.

So the SDK is `@amplitude/unified@1.1.32`'s own UMD build, byte for byte, at
`static/vendor/amplitude-unified.umd.js`, served by the queue server at
`/vendor/` and embedded into the standalone `karaoke_os.py` beside the page.
The page loads it by a relative path, so the pinned-hosts test still passes,
and every analytics call is guarded so a phone whose SDK did not load still
joins. The events themselves still need an uplink to reach Amplitude; when
there is none they are queued in the browser and dropped, and the singer
never notices. That is the trade: a 779 KB file in the repo for a page that
degrades to exactly what it was.

The ingestion key is inline in the page with the wizard's own label — an
ingestion key is public by design — because the standalone build runs on
whatever laptop the venue has, where an environment variable is a burden on
someone who is not the owner.

## One event, chosen by the owner

`Joined Queue`, fired once in the `/join` success callback, carrying the
setup flow's `prompt_version`. Autocapture covers the rest. Nothing fires on
the error path, and nothing was invented beyond the one event.

## The Python side is the manual route

`pwb_toolbox/vision/telemetry.py` wraps `VisionClient.ask` in an agent
session. `amplitude-ai` has no NVIDIA wrapper, so it is the manual
`track_user_message` / `track_ai_message` path the SDK's instructions
prescribe for an OpenAI-compatible proxy. `docs/nvidia-vision.md` has the
details, including why cost is reported as zero.

Candidly: almost every page in this repository has one user. The karaoke
queue was chosen because it is the exception, and the vision client because
it is the only LLM call site. Neither had a first event at the time of
writing; the ledger holds the step that proves one lands.
