# Letting an agent drive TradingView: what you are actually exposing

`tradingview-mcp.md` covers how to connect Claude to TradingView Desktop. This file
covers whether you should, and on which account. It exists because the question came
back a second time about a different tool, and the answer is a property of the
*approach* rather than of any one vendor — so it is worth writing down once instead
of re-deriving it per app.

Short version: the interesting risk is not that a developer steals your money. It is
that you end up with an LLM holding a live Buy button, and that the debug port which
makes any of this work has no authentication on it at all.

## The whole mechanism is one open port

Every tool in this class works the same way. TradingView Desktop is an Electron app,
so it speaks the **Chrome DevTools Protocol**. Started with
`--remote-debugging-port=9222`, it exposes a debugger on `127.0.0.1:9222`, and the
tool attaches to that and evaluates JavaScript inside your logged-in chart.

That is the entire trick. It is also the entire threat model, because **CDP has no
authentication**. There is no token, no handshake, no per-client permission. Anything
on your machine that can open a TCP connection to that port gets everything below —
the vendor's app, a second copy of the same tool, an npm `postinstall` script, a
browser extension with localhost access, anything you ran once and forgot.

## What that port grants, concretely

Once attached, a client can call `Runtime.evaluate` in the renderer of a page you are
logged into. That means it can do anything the page itself can do:

- **Read the session.** `document.cookie`, `localStorage`, `sessionStorage`. A
  TradingView session token is a bearer credential — whoever holds it is you, from
  anywhere, until you log out everywhere and it is revoked.
- **Call TradingView's own APIs as you.** `fetch(..., {credentials: 'include'})` from
  inside the page reaches `pine-facade.tradingview.com`, the alerts service, watchlist
  endpoints. Not just reads: writes too.
- **Drive the UI.** Synthesise clicks on any control the page renders. Including, if
  the Trading Panel is open and a broker is linked, the order ticket.
- **Navigate.** `Page.navigate` points that logged-in window anywhere.
- **Screenshot.** `Page.captureScreenshot` of whatever is on screen.

None of that requires a bug or bad intent. It is the documented, intended power of a
debugger, which is why the port is off by default and why turning it on is a decision
rather than a setting.

## The risks, ranked by probability times cost

**1. The agent places a real order.** This is the one to actually worry about, and it
involves no villain. TradingView's Trading Panel puts Buy and Sell in the same DOM as
the chart the agent is reading. An agent told to "test this on the live chart" is one
ambiguous instruction away from a filled order, and there is no second confirmation
standing between a synthesised click and a live broker. Probability: moderate and
rising with how much you automate. Cost: bounded by your position size, not by your
intent.

**2. Session-token exfiltration.** Any tool with CDP access *can* read your
TradingView session and send it somewhere. Whether a given tool *does* is exactly the
question source code answers and a closed binary does not. Note what is and is not at
stake: your broker password is not in TradingView — the broker link is an OAuth grant
held at the broker — but the session that holds that grant is, and that is enough to
act on it.

**3. Your Anthropic credential.** Tools in this class need Claude. Pasting an API key
into a closed-source app hands a billable credential to code you cannot read; signing
in with a Pro/Max account is meaningfully better, because that token is scoped and you
can revoke it from your own account page without rotating anything else.

**4. Everything else on your machine.** Covered above: while the port is open, the app
you audited is not the only thing that can use it.

**5. TradingView's Terms.** Automated collection and non-display use are restricted.
The open-source project's own README says CDP interaction may conflict with those
terms and that you assume all risk, **including account suspension**. That exposure
lands on the subscription you pay for, and it is identical whichever tool you use.

## The mitigation that actually works: two logins

Every other control is fiddly and most are theatre. This one is cheap and total:

> **The TradingView account an agent can drive must not be the TradingView account
> your broker is connected to.**

Research, Pine authoring, backtesting, replay — on a login with no broker link. Order
entry — somewhere else, ideally not in TradingView at all. Then risk 1 goes to zero by
construction rather than by the agent behaving, and risk 2 degrades from "someone can
trade my account" to "someone can see my watchlist."

This costs you a free second TradingView account and the discipline to keep them
apart. It is the whole recommendation; the rest of this file is detail.

## Open source is not "safer". It is checkable.

The distinction matters, because closed-source is not an accusation:

- A **readable** tool lets you answer "does it phone home" by reading it, and lets you
  re-answer it after every update by reading the diff.
- A **closed** tool means you are trusting a stated policy. That can be perfectly
  reasonable — you trust stated policies constantly — but it is a different kind of
  confidence, and it should scale with what is at stake behind the account.

An **unsigned** installer is worse than either, because it removes your ability to
tell the developer's build from someone else's. When an app's own instructions are
"you will see a SmartScreen warning, click More info then Run anyway," that warning is
Windows correctly reporting that no certificate authority has bound the file to a
named legal entity. Click through it if you choose — but record the SHA256 afterwards,
because it is the only fingerprint you will have.

## What was verified here about `tradesdontlie/tradingview-mcp`

Audited 2026-08-20 by reading the source at commit `c05b8f5`. Method: grep the whole
`src/` and `scripts/` tree for outbound calls, telemetry SDKs, and credential access.
Findings:

- **No third-party egress.** Every `fetch` resolves to one of two places:
  `127.0.0.1:9222` (the CDP port), or a `tradingview.com` host called *from inside the
  page* with your session. Nothing addresses a server belonging to the author.
- **No telemetry.** Zero hits for Sentry, PostHog, Mixpanel, Segment, Amplitude,
  analytics, or any usage reporting.
- **No credential access.** Zero hits for `document.cookie`, `localStorage`,
  `sessionStorage`, `Network.getAllCookies`, or any session-token name.
- **CDP domains enabled are `Runtime`, `Page`, `DOM`** — notably not `Network`, so it
  does not observe traffic.
- **`replay_trade` is replay only.** Its buy/sell/close go to
  `TradingViewApi._replayApi`, TradingView's bar-replay simulator. There is no broker
  code anywhere in the tree.
- **Two runtime dependencies** (`@modelcontextprotocol/sdk`, `chrome-remote-interface`).
  The 173 packages in the lockfile are almost entirely `eslint`'s dev tree.
- **Self-update is `git fetch` plus a fast-forward** of `origin/main`, guarded against
  dirty trees, non-`main` branches and non-git installs. So updates arrive as commits
  you can read, not as a binary from a vendor server.

Two things that are *not* clean bills of health, and belong in any honest summary:

- It is **not read-only.** `watchlist.js` POSTs to `tradingview.com` to remove symbols
  from custom lists, using your session. It changes your account state.
- None of the above constrains what *else* can reach port 9222 while it is open.

## What is known about The Trade Companion, and what is not

Free Windows desktop app from Tyler Bundy of The Purpose Driven Trader, at
`desk.thepurposedriventrader.com`. Same architecture as above: connects Claude to your
TradingView Desktop over the local debugging interface. Sign in with a Claude
subscription or paste an Anthropic API key. Email required to download.

**None of that has been verified.** It is the vendor's own description. The site is
blocked from Claude Code cloud containers by the egress proxy, so no cloud session can
read the page, download the installer, or inspect the bundle — this has to happen from
a local session or from the audit script below. Recorded so the next session does not
spend the round trip rediscovering it.

What can be said without inspecting it: its stated claims are self-consistent and
match how this class of tool necessarily works; "reads charts, cannot touch your
broker or your money" is true of the code the developer writes and false of the port
that code opens, which is the distinction this whole file is about. The business model
appears to be a funnel — free tool, email capture, prop-firm content — which is
ordinary and is a marketing consideration rather than a security one.

## Auditing a closed-source app yourself

`tools/audit_electron_app.ps1` reads an installed Electron bundle off disk without
running it, and reports publisher identity and SHA256, every URL literal compiled into
the bundle (bucketed so non-TradingView, non-Anthropic hosts stand out), whether the
bundle contains credential-reading code, and whether it can auto-update. `-Live` adds
a snapshot of the app's established outbound connections while it sits idle.

```powershell
cd C:\Users\Gexio\OneDrive\pwb-toolbox
.\tools\audit_electron_app.ps1 -Name companion
```

Read section 4 first. If the app auto-updates, the audit expires with the next
release, and the SHA256 in section 1 is the thing to re-check.

A clean report proves that the obvious exfiltration routes are not present in plain
text in that build. It cannot see code fetched at runtime, anything packed or
obfuscated, or a decision made server-side. It catches carelessness, not a determined
adversary — and it says nothing at all about risk 1, which stays exactly where it was.

## Port hygiene, whichever tool you pick

- Start TradingView with the debug flag only when you are about to use the agent, and
  close it fully afterwards. Do not leave it in connected mode all day.
- Bind to `127.0.0.1`, never `0.0.0.0`. Never port-forward 9222. Never expose it to
  the LAN "just to test from the laptop."
- Keep the agent-drivable login broker-free. See the two-login rule above.
- Re-run the audit after any version bump of a closed-source tool.
- If you paste an Anthropic API key anywhere, scope it and be ready to rotate it at
  the Anthropic console. Prefer subscription sign-in where the tool offers it.
