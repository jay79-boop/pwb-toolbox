# Connecting Claude to TradingView

This is not part of `pwb_toolbox`. It is written down here for the same reason the
design tooling is: it is a thing sessions in this repo are asked to set up, and the
setup has two traps that cost a round trip each time they are rediscovered.

The tool is [`tradesdontlie/tradingview-mcp`](https://github.com/tradesdontlie/tradingview-mcp),
MIT-licensed, an MCP server that lets Claude Code read and drive TradingView Desktop —
chart state, indicator values, the Pine editor, replay mode. It does not place orders.

## How it works, and why TradingView must be closed first

TradingView Desktop is an Electron app, so it speaks the **Chrome DevTools Protocol**.
The server connects to it on `127.0.0.1:9222` and reads the chart's own data structures
rather than a screenshot.

The debug port only exists if the app was *started* with `--remote-debugging-port=9222`.
You cannot add the flag to a process that is already running, which is why every setup
guide for this — including the commercial ones — opens with "make sure TradingView is
closed." The launcher kills any running instance and relaunches it with the flag. If
that instruction looks arbitrary, that is what it is for.

Use `127.0.0.1`, not `localhost`. On machines where `localhost` resolves to IPv6 `::1`
the connection fails, because Electron's debug server does not listen there.

## Whether to do this at all

The setup below is the *how*. `tradingview-agent-security.md` is the *whether*: what
an open debug port actually grants, why the account you point this at should not be
the one your broker is linked to, and what reading this project's source did and did
not establish. Read it once before connecting anything that holds money.

## The terms-of-use exposure

TradingView's Terms restrict automated data collection and non-display usage. The
project's own README says CDP interaction may conflict with those terms and that the
user assumes all risk, **including account suspension**. That risk is a property of the
approach, not of this particular implementation — every tool that does this, free or
paid, carries it. Decide on it separately from deciding on the tooling.

## Install (Windows)

Clone outside OneDrive. Git repositories inside OneDrive hit the `.git/objects` locking
prompt described in `CLAUDE.md`, and syncing 173 npm packages is pure waste.

Needs Node.js 18+ and TradingView Desktop already installed.

```powershell
git clone https://github.com/tradesdontlie/tradingview-mcp.git C:\Users\Gexio\tradingview-mcp; cd C:\Users\Gexio\tradingview-mcp; npm install
claude mcp add tradingview --scope user -- node C:\Users\Gexio\tradingview-mcp\src\server.js
```

`--scope user` registers it for every project, not just this one. Restart Claude Code
afterwards — MCP servers are only read at startup.

Then ask Claude to run `tv_launch`, and confirm with `tv_health_check`. A healthy
response has `cdp_connected: true` and names the symbol currently on your chart.

## The MSIX trap

TradingView for Windows now ships **only as an MSIX package**, installed under
`C:\Program Files\WindowsApps\`. Some Windows builds refuse to launch an executable
from there with **"Access is denied"**, which reads like a permissions bug you should
fix. Do not try to fix it: `icacls` on `WindowsApps` fails and can break app servicing.

`tv_launch` already handles it — it copies the package to `%LOCALAPPDATA%\tradingview-mcp\`
once (~330 MB, keeps your login and layout) and launches from the copy. The result
reports `msix_local_copy: true` when that path was taken. This is why `tv_launch` is
the recommended route over `scripts\launch_tv_debug.bat`, which cannot do the fallback.

## Port hygiene

While TradingView is running in connected mode, anything on your machine that can reach
`127.0.0.1:9222` can drive it and read its logged-in session. Localhost-bound, so not
remotely exposed by default — but do not forward the port, and close TradingView when
you are done rather than leaving it in connected mode all day.

## Verifying a change without a Windows machine

`npm run test:unit` runs offline. From a Linux container expect **21 of 23 suites to
pass**: the two `pine check`/`pine_check` suites need TradingView's compile API and get
403 through the egress proxy, and the two Windows launch suites skip by design. Only
the launch path genuinely requires Windows to exercise.
