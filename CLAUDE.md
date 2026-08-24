# pwb-toolbox

A toolbox library for quant traders: datasets, backtesting (Backtrader), live
execution, and performance analytics. Requires Python 3.10+.

## Where the work happens

The owner works in **Claude Code running locally on their Windows machine**, in
this repository, and talks to it in plain English rather than pasting shell
commands. Away from the desk they do not start a session somewhere else — they
reach that same local session from their phone, over Remote Control, which is
what lets them approve things without being at the keyboard. A scheduled task,
`\ClaudeRemoteControl`, keeps one alive from logon so there is always something
to attach to.

Cloud sessions do still exist, and this file is read by them, so the capability
gap between the two is load-bearing — getting it wrong cost two days once. A
local session can touch `C:\Users\Gexio\...`, run their venv, and open the
trade journal. A cloud session shares only GitHub with them: it cannot read
their disk, their scheduled tasks or their skills directory, and everything on
that side has to be done by handing them a command and waiting for the output.
Both see this identical repository, which is exactly why the two are easy to
confuse. The `gexio-machine` skill carries the diagnostic for working out which
one you are in — run it before writing a single command for them.

They do not retain procedures between sittings, and should not have to. The
orientation hook has every session open with a short unprompted catch-up — branch
state, unpushed work, open PRs and their CI, anything unfinished, one suggested
next step — so "where was I" is answered before it has to be asked.

**"Catch me up"** is how they ask for that same thing again mid-session, when
the thread is lost. It wants where things stand right now — branch, working
tree, what is in flight — not a replay of how it got there.

## Brainstorm before building

The owner asked for this after several rounds where a guess was shipped and they
found the hole in it: a spreadsheet that reported a portfolio value four tenths
of which had not moved since 2022, a ladder that collapsed to `#N/A` on a ticker
typed by hand, a tab whose frozen header hid everything under it.

So for anything beyond a small correction: explore what they are actually trying
to do, put the trade-offs to them, and get an answer before writing code. Ask few
questions and make them count — use `AskUserQuestion` so they can click rather
than type, lead each option list with a recommendation, and say which one you
would pick and why.

**Put it in a box they can click.** Anything that can be a choice should be one:
`multiSelect: true` wherever more than one answer can be true at once, so they
tick what they want instead of composing a reply. Their answers are often
combinations — "do 1 but incorporate 2 and 3" — and a checkbox list gets that in
one click. Prose is for the two cases a box cannot carry: a PowerShell step they
have to run themselves, and an answer only they hold, like a number or a URL.

**Push back.** They want the disagreement, not the compliance. If their framing
has a flaw, say so before building to it. If something they asked for last week
is now dead weight — an empty tab, a feature nothing reads — raise it rather than
maintaining it silently. Recommend the thing you would do if it were yours.

This is a standing preference and it is not limited to this repository. The
cross-project copy belongs in the `gexio-machine` skill, which is synced from
their account: a cloud session cannot durably edit it, so that copy has to be
written from a local session or pasted by them.

## Flagging action items

Anything the user has to do themselves — export a key, restart something, click
through an OAuth flow, make a decision — goes in one `## 🔴 NEEDS YOU` block at the
very end of the reply, never buried in a paragraph.

How to write that block lives in the `gexio-machine` skill: the heading and numbering
rules, the one-self-contained-paste rule, naming the program each step goes into, what
success looks like, GUI steps, prompting with `Read-Host` instead of asking them to
hand-edit a command, and the PowerShell traps (`curl` aliased to `Invoke-WebRequest`,
no `sed`, no `~`, no `&&`, `Add-Content`'s encoding, pinning
`raw.githubusercontent.com` to a commit SHA). The skill loads before any command
written for the user to run. Those rules are deliberately not restated here: two
copies drift, and the skill's copy is the one that reaches sessions outside this
repository.

One rule that is not in the skill: if a step is also explained in the prose above, it
still gets repeated in the block — the block is the checklist of record.

A worked example, for this repo's 21st MCP key:

````
## 🔴 NEEDS YOU

1. **Open PowerShell** — press `Win`+`R`, type `powershell`, press Enter. A window
   opens with a `PS C:\Users\Gexio>` prompt.
2. **Set the key** — paste this one line, press Enter, then paste your key at the
   prompt it shows:
   ```powershell
   [Environment]::SetEnvironmentVariable('API_KEY_21ST', (Read-Host 'Paste your 21st.dev API key'), 'User')
   ```
   It prints nothing when it works.
3. **Restart Claude Code** — close that terminal window entirely and open a new one,
   then run `/mcp` and choose `21st` from the list. Windows that were already open
   will not see the new variable.
````

## Layout

- `pwb_toolbox/` — the shipped package (`datasets`, `backtesting`, `execution`, `performance`, `scraping`, `converting`, `options`, `journal`)
- `pwb_toolbox_legacy/` — superseded code kept for reference; not part of the public API
- `tests/` — pytest suite
- `tools/ib_server/` — operational scripts for running strategies against Interactive Brokers
- `tools/grok_export/` — exports grok.com chat history to JSON/Markdown (`python -m tools.grok_export`)
- `tools/karaoke_server/` — shared-leaderboard server for `static/karaoke-box.html`; stdlib only
- `tools/market_close/` — renders a daily market-close script for a TTS talking-head avatar
- `tools/analyze_trades.py` — turns a Schwab transaction export into a diagnosis of your trading
- `tools/bill_ladder.py` — settles roll-vs-hold on the T-bill curve, sizes a
  ladder from the maturities Treasury actually sells, and prices the state-tax
  exemption against a savings account. Reads Treasury's daily bill CSV, which
  `home.treasury.gov` blocks from cloud containers — every command takes
  `--rate` overrides so the math still runs offline
- `tools/build_profit_planner.py` — generates the exit-planning workbook (six
  plan tabs bound to one register); prices go live off `GOOGLEFINANCE` once the
  file is opened as a Google Sheet
- `tools/planner_watch.py` — reads that workbook's Watch tab, published as CSV,
  and says when a rung is within reach, a holding has moved, or a position has
  outgrown its limit. Skips anything without a live price rather than alerting
  on a number somebody typed months ago
- `tools/engagement.py` — tracks a business through the AI & automation
  readiness framework (`docs/ai-readiness-framework.md`): twelve gated phases
  from tool audit to go-live, a rendered stakeholder deck, and a cross-engagement
  lessons retro. The `engagement-flow` skill is what actually does the phase
  work; this is the state and the gates. Engagement data lands in
  `engagements/`, which is gitignored because this fork is public.
  `export-flow` writes the engagement as a `flow.json` that
  `static/flow-canvas.html` imports, so an engagement can be seen as a map
- `tools/blueprint_converter.py` — converts a business blueprint
  (`docs/blueprint-schema.json`) between JSON and Excel. The blueprint is the
  shared data model of this trio: `static/blueprint-builder.html` edits one,
  `static/blueprint-dashboard.html` visualizes one read-only, and
  `static/flow-canvas.html` imports one (each process renders as a chain of
  steps). `docs/blueprint-example.json` is a worked example,
  `docs/blueprint-guide.md` the manual
- `tools/backtest_lab.py` — runs one strategy across instruments and vendors and
  says whether the result clears its own noise floor. Reads a feed's timezone
  correctly (see "Backtesting" below), normalises to basis points, and compares
  two vendors of the same instrument — the check that decides whether a
  single-instrument result meant anything
- `tools/vwap_lab.py` — runs the VWAP setups (band-fade mean reversion,
  pullback, and the crossover kept as a control expected to fail) over one or
  two vendor feeds through `backtest_lab`'s cost-charging and noise-floor
  machinery. The strategy itself is `pwb_toolbox/backtesting/vwap.py`
  (session/anchored VWAP with volume-weighted σ bands + `VwapStrategy`), the
  TradingView reading is `pine/vwap_strategy.pine`, and the evidence behind
  the setup choices is sourced in `docs/trading-wisdom.md`. Warns when a feed
  carries no volume (histdata index CFDs), because VWAP silently degrades to
  TWAP there
- `tools/graph_audit.py` — audits a graphify knowledge graph against this repo's actual imports
- `tools/kronos_lab.py` — measures the Kronos K-line foundation model
  (shiyu-coder/Kronos) before trusting it: walk-forward scorecard (direction
  hit rate with exact p-value, information coefficient, error vs persistence)
  plus a forecast-chart mode. Model runs happen on the user's machine — the
  cloud proxy blocks Hugging Face — but the scoring core is pure math and
  tested with fake predictors (`tests/test_kronos_lab.py`)
- `tools/crypto_scan.py` — the "trade crypto" command: ranks liquid crypto
  pairs by the signals with published evidence behind them (1–4 week momentum,
  MA trend, volume surge; ATR% for sizing only) and flags the BTC regime every
  alt trade swims in. A screener feeding the pre-trade pack and paper journal,
  not a trader. Scoring is pure math tested on synthetic bars
  (`tests/test_crypto_scan.py`); signal choices are sourced in
  `docs/trading-wisdom.md`
- `tools/spec_desk.py` — the "trade spicy" desk: ledger and rules engine for
  the walled-off high-risk paper pot (four lanes: 15–45 DTE option buys,
  sub-capped 0–7 DTE lotteries, momentum stocks, defined-risk credit
  spreads). Caps per-trade loss at 10% of the pot, locks the desk when the
  pot is spent until `review` runs, scores every close in R-multiples, and
  `check` alerts when an open trade's stop or target level trades. Protocol
  in `docs/spec-desk.md`; ledger data in `spec_desk/` (gitignored — this
  fork is public). Rules engine is pure and tested (`tests/test_spec_desk.py`)
- `tools/night_lab.py` — the "good night" command: unattended trade stress
  testing between 1am and 8am, driven by a local Ollama model. The design rule
  is **the model proposes, Python computes** — an LLM cannot calculate a
  drawdown, so it only ever generates hypotheses (thesis attacks, shock
  scenarios, suspected leaks) and deterministic arithmetic produces every
  number from the real record. Anything unparseable or uncheckable is dropped
  rather than repaired. Yields the machine the moment you touch the keyboard
  and checkpoints after every job, so an interruption costs one job, not the
  night. Backtests feed it too: `reversal_15m_sim.py --trades-out` exports
  closed trades that `plan --sim` merges into the night's record, so a
  strategy is stressed before it ever risks paper money. Protocol in
  `docs/night-lab.md`; data in `night_lab/` (gitignored —
  this fork is public). Policy and arithmetic are pure and tested
  (`tests/test_night_lab.py`)
- `tools/season_scan.py` — the seasonality lab: batches any universe of
  tickers (11 sector ETFs, index baselines, crypto, `season/universe.txt`
  names) and makes every ticker-month cell survive three gates before it may
  call itself a pattern — 2,000 within-year permutation reshuffles, split-half
  agreement across the years, and Benjamini-Hochberg FDR across the whole
  grid, because a 15x12 scan is a fishing expedition and is priced as one.
  Named almanac claims (sell in May, September weakness, the January effect)
  are pre-registered so they face only their own one-sided test, and get a
  public HELD/FAILED verdict. Outputs a self-contained visual report
  (heatmap, average-year run/dip paths with both halves overlaid, now-window
  screener), a sectioned TradingView watchlist, and `season.json` for other
  tools; `context SYMBOL` answers "where does today sit in this ticker's
  year?". Also splits the *other* calendar — the one inside the day: every
  ticker's return decomposed into close-to-open and open-to-close, gated the
  same three ways, with the round trip an overnight-only position pays
  **every day** charged against it and the best-few-nights concentration
  printed beside it. Protocol in `docs/season-scan.md`; data in `season/` (gitignored —
  universe.txt names what the owner watches). Stats are pure and tested on
  planted synthetic data (`tests/test_season_scan.py`)
- `tools/spicy_lab.py` — Excel export and quote helper for the spicy lab:
  `excel` writes the move ladder workbook for one contract (rungs × time
  columns, greeks, shot clock, hurdle) through `pwb_toolbox.options`; `serve`
  is the loopback-only stdlib quote helper (port 8877, CORS for file://) that
  lights up the lab page's Refresh button. Ladder math and quote handling are
  pure and tested (`tests/test_spicy_lab.py`)
- `static/spicy-lab.html` — the speculative desk's visual instrument: enter
  one contract, see the shot clock and hourly hurdle, a move ladder (rungs ×
  expected-move or fixed %, columns marching through time), greek attribution
  bars for any move/minutes/IV scenario, and premium-velocity slices with an
  exit-tell verdict. Opens from `file://`, loads `option-lab.js` from the same
  directory (never duplicates its math), saves inputs to localStorage.
  Gain/loss pair `#0d9488`/`#ef4444` validated CVD-safe; signs always shown
- `tools/pine_sweep.py` — converts a corpus of real `.pine` files and ranks what blocks them
- `tools/prop_sim.py` — prices a prop-firm evaluation against its own
  published rules: pass probability, attempts and cost to funded, EV per
  attempt, for a coin flip or a sim's real exported R-distribution. Monte
  Carlo pinned against gambler's-ruin closed forms; demonstrates that sizing
  moves pass odds only *through* the rules (time limits, consistency,
  trailing drawdowns), not on the pure walk. Protocol and honesty caveats in
  `docs/prop-sim.md`; intel source in `docs/field-notes-prop-firms-and-data.md`
- `tools/calibration_audit.py` — when Black-Scholes said 30% touch, how
  often did it actually touch? Runs this repo's own finish/touch
  probabilities over years of real daily bars: barriers placed in sigma
  units so every row is an exact binomial test, non-overlapping windows,
  trailing volatility versus a static benchmark, BH across the rows. Reads
  the bars `season_scan fetch` already writes. The window's own realized
  volatility is deliberately *not* offered as a mode — self-normalization
  would report a fat-tailed series as thin-tailed. Manual in
  `docs/calibration-audit.md`; tested against synthetic data the model is
  right about (must convict nothing) and wrong about (must convict the far
  barriers) in `tests/test_calibration_audit.py`
- `tools/reversal_15m_sim.py` — executable second reading of the 15-Minute Reversal
  rules in `pine/`. Pine cannot be run from a container, so this is what a rule change
  gets checked against; it emulates TradingView's intrabar path assumption so a low
  printed before the entry filled cannot retroactively stop the trade out.
  `--fragility-out` sweeps bar size (15/30/45/60, resampled on a grid
  anchored at the opening candle so widening the bars cannot silently erase
  it) alongside rr and sma-length, because a strategy can be fitted to a
  timeframe as easily as to a number
- `tools/trade_card.py` — pre-trade commitment card and hold-time checker for long single-leg options
- `tools/audit_electron_app.ps1` — reads a closed-source Electron app off disk without
  running it and reports signature, every host baked into the bundle, credential-reading
  code and auto-update. PowerShell, for the user's machine; see
  `docs/tradingview-agent-security.md`
- `tools/desk_agent/` — the unattended agent: a playbook, five job files, a run log
  it writes after every run, and a weekly review that revises the playbook from that
  log and opens a draft PR. Guardrails live in a section of the playbook the review
  is forbidden to edit. `tools/register_desk_agent.ps1` registers the Windows
  scheduled tasks; see `tools/desk_agent/README.md`
- `tools/spend_watch.py` — audits a `list_sessions`/`list_triggers` snapshot for
  the patterns that exhaust a usage window: Routines that re-arm themselves into
  a persistent session, wakes bound to a session too fat to load cheaply, and
  too many sessions live at once. It will **not** derive a burn rate from a
  single snapshot — session metadata reports lifetime totals, so a rate needs a
  `--baseline` to diff against. Pure functions, tested on synthetic snapshots
  (`tests/test_spend_watch.py`); protocol in `docs/spend-safety.md`
- `static/flow-canvas.html` — process-mapping tool (a clean-room redesign of
  puzzleapp.io's workflow canvas): drag-and-connect step cards, wait, end and
  go-to steps, status/owner coloring, layered auto-layout, undo, and
  Paper/Slate themes, plus a monthly person-time figure and a checks panel
  holding the map to the standard. Opens from `file://` and loads
  `process-grammar.js` from the same directory — no build step, but it is no
  longer one file. Saves to localStorage, exports JSON. Import accepts its own
  exports, `engagement.py export-flow` files, and business blueprints
  (`docs/blueprint-schema.json`). Design spec in
  `docs/specs/2026-08-22-flow-canvas-design.md`
- `static/process-grammar.js` — the branch grammar in one place: the checks
  (unlabelled branches, branches pointing nowhere, forks with one way out,
  long loop-backs that should be go-to steps, unpriced person steps), the
  duration parser, the layering, the load rollup, and the renumber-and-repoint
  logic. `flow-canvas`, `blueprint-builder` and `blueprint-dashboard` all load
  it rather than keeping a copy, and `tests/test_process_grammar.py` holds it
  against `check_process` in `tools/blueprint_converter.py` case for case, so
  a browser tool cannot call a map finished that the validator then rejects.
  The rules themselves are in the `process-mapping` skill
- `static/journal-shots.js` — chart screenshots for the journal: downscale and
  re-encode on the way in, then account the result against the ~5 MB localStorage
  a `file://` page gets. The arithmetic is what is tested (`node
  static/journal-shots.test.js`, run under pytest by `tests/test_journal_shots.py`);
  `shrink()` needs a canvas and is verified in a browser instead
- `static/option-lab.js` — Black-Scholes, greeks, decay and profit ladders in the
  browser, with no dependencies. A port of `pwb_toolbox/options/{greeks,decay}.py`
  kept deliberately faithful: `tests/test_option_lab.py` prices a spread of
  contracts through the Python module and requires node to agree to 1e-9, so the
  two cannot drift into disagreeing about the same contract. Adds what Python has
  no counterpart for — rho, touch and finish probabilities, and the ladders —
  tested against closed forms in `static/option-lab.test.js`. Also home to
  `attribution` — splits a repriced premium change into delta/gamma/theta/vega
  dollars with the unexplained part reported as residual — which the spicy lab
  leans on
- `pine/` — TradingView strategies kept as reviewable source; `README.md` there covers
  the chart setup they need. Nothing under `pwb_toolbox/` imports them
- `docs/trading-wisdom.md` — the sourced knowledge base behind the desk: ten
  machine-enforceable risk rules with their originating traders/papers, the
  retail base-rate studies that justify paper-first, the evidence review
  behind `crypto_scan`'s signals, iron condor venue/construction facts, and
  the propose-then-approve learning loop. Trading sessions and the desk agent
  consult it; it grows by proposal, never by silent edit
- `docs/` — `datasets.md`, `backtesting.md`, `execution.md`, `scraping.md`, `converting.md`,
  `ai-readiness-framework.md` (the engagement playbook `tools/engagement.py` tracks), plus
  `index.html` (the published landing page; see "Design tooling" below),
  `tradingview-mcp.md` (connecting Claude to TradingView Desktop over the Chrome
  DevTools Protocol — unrelated to the library, written down because the setup has
  traps that otherwise get rediscovered every time),
  `tradingview-agent-security.md` (whether to point an agent at TradingView at all,
  and on which account — the CDP threat model, the two-login rule, and what was
  actually verified about the open-source bridge by reading its source),
  `agent-fleet.md` (critique and design of the owner's multi-agent fleet — the
  operating procedure itself is the `agent-fleet` skill under `.claude/skills/`)
  and the spend-safety pair — `token-drain-2026-08-24.md` (what exhausted a
  five-hour window, measured rather than guessed) and `spend-safety.md` (every
  surface that can reach a card, ranked by worst case, and the five layers that
  bound them; the rules themselves are the `spend-safety` skill)

## Environment

Dependencies live in `requirements.txt`; `requirements-dev.txt` adds `pytest`.
CI (`.github/workflows/tests.yml`) runs two jobs on Python 3.11: `test`
(`pytest tests/ -v`) and `format` (`black --check --diff pwb_toolbox/ tools/
tests/`). They are separate jobs so a formatting nit cannot mask a real test
failure, or the reverse.

`.github/workflows/delete-merged-branch.yml` deletes a pull request's head branch
once it merges. This exists because branch deletion is the one git operation a
Claude Code container cannot perform — the session's git proxy returns **403** for
any ref deletion — so merged branches otherwise pile up until someone clears them
by hand from a local checkout. GitHub's runner is not behind that proxy. It skips
fork branches and unmerged closures, and treats an already-deleted ref as success.
It needs Settings → Actions → General → **Workflow permissions** set to "Read and
write permissions"; without that the job fails with a message saying so. GitHub's
built-in "Automatically delete head branches" checkbox does the same thing, and
this is a file instead so the behaviour is reviewable rather than invisible
repository state.

A branch that is still in use simply comes back: the next `git push` from the
session recreates it, which is what already happens when one is deleted by hand.

In Claude Code on the web, `.claude/hooks/session-start.sh` does this setup
automatically: it builds a `.venv/` (the system interpreter has Debian-managed
packages such as `cryptography` that pip cannot upgrade), installs
`requirements-dev.txt`, and exports `PATH` and `PYTHONPATH` so bare `python`,
`pytest`, and `black` resolve to that venv. The hook is a no-op in local
sessions, which manage their own environment.

It runs asynchronously, so the session is usable immediately while packages
install in the background — roughly a minute on a cold container, ~2s once it
is warm. If `pytest` or an import of a third-party package fails in the first
moments of a session, the install is most likely still running; re-run the
command rather than treating it as a real failure.

To set the same thing up by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH="$PWD"
```

The tests import `pwb_toolbox` from the repo root rather than from an installed
distribution. `pythonpath = ["."]` under `[tool.pytest.ini_options]` in
`pyproject.toml` covers that for `pytest` itself (including in CI, which sets no
`PYTHONPATH`); the exported `PYTHONPATH` above covers ad-hoc invocations such as
`python -c "import pwb_toolbox"`.

## Commands

```bash
pytest tests/ -v                  # full suite (~28s cold / ~15s warm)
pytest tests/test_optimal_limit_order.py -v
python tools/trade_card.py plan --help    # pre-trade card + hold-time checker
python tools/analyze_trades.py export.csv # diagnose a Schwab transaction export
python tools/bill_ladder.py compare --roll-rate 3.70 --hold-rate 3.81  # roll vs hold
python tools/reversal_15m_sim.py bars.csv           # 15-Minute Reversal over a bar CSV
python tools/prop_sim.py evaluate --risk 200  # price a prop eval (demo rules)
python tools/spend_watch.py audit snapshot.json  # what is draining the window
python tools/build_profit_planner.py --out planner.xlsx  # crypto exit-planning workbook
python tools/engagement.py list   # readiness engagements and where each stands
python -m tools.desk_agent.runlog summary --last 20   # is the agent actually working
python -m tools.desk_agent.runlog review  --last 40   # what the weekly review reads
python tools/night_lab.py plan    # queue tonight's overnight stress jobs
python tools/night_lab.py verdict --quiet  # morning findings; silent if none
python tools/season_scan.py report  # seasonality: report + watchlist + json
python tools/season_scan.py overnight       # overnight vs intraday, per ticker
python tools/calibration_audit.py --symbols SPY   # is our option math calibrated?
node static/option-lab.test.js    # greeks/ladder math (also run by pytest)
node static/journal-shots.test.js  # screenshot sizing/budget (also run by pytest)
node static/process-grammar.test.js  # branch grammar (also run by pytest)
black pwb_toolbox/ tools/ tests/  # format; CI checks this exact scope
black --check --diff pwb_toolbox/ tools/ tests/   # what CI runs
```

## Conventions

- Formatting is `black` with default settings — see `.vscode/settings.json`,
  which also enables pylint with `--disable=relative-beyond-top-level`.
  `black pwb_toolbox/ tools/ tests/` is the formatted surface, is clean, and is
  gated by the `format` CI job. Do **not** run bare `black .`: it would also
  rewrite `pwb_toolbox_legacy/` (superseded, kept as-is for reference) and the
  vendored skill under `.claude/skills/`, which tracks upstream and is restored
  by `uipro init` — neither is in the gated scope.
  `black` is pinned in `requirements-dev.txt`; its stable style changes with
  each January release, so an unpinned formatter would eventually fail CI on
  code nobody touched. Bump the pin deliberately, reformatting in the same commit.
- Do **not** pin an exact test count in this file. Every branch that adds a test
  has to touch that one line, so any two open PRs collide on it — three of the
  merges in August were conflicts on nothing else, and the number was stale by
  thirty within a day of the last fix. `pytest` prints the real total; the
  timing hint in Commands is what the line is actually for.
- Tests must not require network access or a live broker. `ib_insync` calls are
  exercised against a mocked `IB` client (see `tests/test_ib_connector_calibration.py`),
  and dataset tests should not depend on `PWB_API_KEY` or a Hugging Face login.
  `pwb_toolbox.scraping` follows the same rule: HTTP is served by a fake session
  and `PoliteSession` takes injectable `sleep`/`monotonic` so rate-limiting and
  retry behavior can be asserted without real delays (see `tests/test_scraping.py`).
- `pwb_toolbox.converting` emits Backtrader source, so its tests compile the
  generated code and run it through a real `cerebro` on synthetic bars — a
  conversion that parses but does not execute or trade is a failure, not a pass
  (see the end-to-end section of `tests/test_converting.py`).
- Regression tests for fixed bugs pin the previous numeric output where the old
  behavior must be preserved (see `_LEGACY_DEFAULT_QUOTE` in
  `tests/test_optimal_limit_order.py`).

## Verifying converter work without the user

Do not use the user as a test harness. Every round trip that asks them to paste
a command, report output, and wait costs them far more than it costs us, and
most of what it found was reachable from here.

From this container: `raw.githubusercontent.com` and plain `git clone` both
work. `api.github.com` and `codeload.github.com` return **403** (proxy policy),
so `GitHubSource` cannot be exercised live here — clone instead.

Build a corpus and sweep it:

```bash
mkdir -p /tmp/corpus && cd /tmp/corpus
for r in kohld/tradingview-scripts Tim1l/PineCryptoStrategies \
         casoon/pine-scripts LouisLetcher/quant-pine mihakralj/pinescript; do
  git clone --depth 1 -q "https://github.com/$r.git" &
done; wait
cd - && python -m tools.pine_sweep /tmp/corpus --strategies-only
```

`tools/pine_sweep.py` converts every `.pine` under a directory and ranks the
failure reasons by how many scripts each costs, so the next thing to fix is a
measurement rather than a guess. Always pass `--strategies-only` for a
meaningful number: one indicator library (`mihakralj/pinescript`, 410 files)
outnumbers the actual strategies in that corpus twenty to one and drags the
headline figure somewhere useless.

A non-zero crash count is a bug in the converter, not a fact about the corpus.
`convert` is contracted never to raise.

## The desk agent runs itself, and reports on itself

`tools/desk_agent/` is a Claude Code agent that runs on a schedule and revises its
own playbook. Two things about it are worth knowing before touching it, because both
are deliberate and both look like oversights.

**The run log is committed.** `runs.jsonl` is one JSON line per unattended run. It is
tracked rather than ignored because it is the only part of the agent a cloud session
can see, and because `git log` over it is the audit trail. Raw stdout under `logs/`
is ignored — that is noise, and it can carry chart detail.

**The agent may not edit its own guardrails, its own log, or `runlog.py`.** The
weekly review rewrites the rest of the playbook freely and opens a draft PR for it,
which is the self-improvement loop. But an agent that can loosen its own limits does
not have limits, and one that can edit its own record of what happened cannot be
reviewed — so those three are walled off, and a review that wants to change them has
to stop and say so instead.

The autonomy ceiling is "everything except order entry", on a TradingView login with
no broker connected. The reasoning behind that specific line, and why it beats
auditing the tooling, is in `docs/tradingview-agent-security.md`.

## Backtesting: two things that produced confidently wrong answers

`tools/backtest_lab.py` exists because a single-instrument backtest in this
repo produced a result that survived every check applied to it and was still
wrong twice over. Both failures are cheap to repeat and neither announces
itself, so both are pinned by tests in `tests/test_backtest_lab.py`.

**A feed's timestamps are in whatever zone the vendor chose, and the file does
not say.** histdata's ASCII M1 exports are stamped **New York local time with
DST**, not the fixed EST they are usually assumed to be. Read with one flat
offset they are right in January and an hour out in July, and since a strategy
converts back to an exchange timezone to test its session, that hour moves the
whole trading window for eight months of every year. The symptom is a result
that looks plausible: an ICT session strategy measured +39 points over eight
years that way, and +7 once the stamps were read correctly.

Do not take a vendor's word for the zone, and do not infer it from a volume
profile — that was ambiguous here. Compare *returns* against a feed whose zone
is known (`verify_timezone`), separately for a winter month and a summer one.
Levels are useless for this: two vendors quoting one index differ by a basis
that swamps the comparison, while their minute returns align at 0.99 and only
at the right offset. **An offset that changes between the two months is DST**,
and the feed is local time. oanda's exports, checked the same way, are UTC.

**A backtest result means nothing until it clears the disagreement between two
vendors of the same instrument.** Running one strategy on the S&P from two
feeds gave +50bp and −234bp over the same eight years — while correlating 0.93
year over year, so both agreed about the shape of every year and disagreed
about the sign of the total. The gap averaged ~35bp/year against a measured
edge of ~6bp/year.

So `noise_floor()` is the check to run before believing any number here: it
takes the same strategy's per-year results from two feeds and reports whether
the edge exceeds the vendor gap. Clearing it is necessary and nowhere near
sufficient. A high correlation with a large mean gap is the dangerous case and
the one actually seen — the feeds look like they agree.

Two habits follow. Compare instruments in **basis points of price**, never raw
points: ten points on \$70 oil and ten on a 20,000 index are not the same
trade, and summing points across instruments produces a number dominated by
whichever quote is largest. And **charge costs always** — the strategy above is
gross-positive and net-negative, so a frictionless run measures nothing
tradeable.

## The trade journal is not in this repository

`trade-journal.html` — the single-file journal that logs a trade against a locked
thesis, closes it against that thesis, and runs the Position Lab — lives only on
the user's machine, at `C:\Users\Gexio\OneDrive\trade-journal\`. It was briefly
committed here so it could be edited, and removed at the user's request: it is a
personal document and this fork is public.

Two consequences worth knowing before working on it:

- **Ask for the file, do not reconstruct it.** `git log --diff-filter=D --
  static/trade-journal.html` finds the commit that removed it, and
  `git show <sha>^:static/trade-journal.html` recovers that revision — but the
  user's copy has moved on since, so treat the history as a reference and the
  file they send as the truth.
- **It inlines `static/option-lab.js` and `static/journal-shots.js` verbatim**, each
  in its own `<script>` block behind a comment saying so. That is deliberate: the journal must stay one file that
  opens from `file://` with no server and no build step, which is what makes it
  usable straight out of a synced folder. Edit the module here, run the tests,
  then re-inline the whole file — never patch the inlined copy, or the tested
  version and the running version stop being the same code.

## The user's local checkout

Windows, PowerShell 5.1, at `C:\Users\Gexio\OneDrive\pwb-toolbox`, Python 3.12.

**A second checkout exists at `C:\Users\Gexio\pwb-toolbox`, without the
`OneDrive`, and the OneDrive one is canonical.** It holds the live feature
branches and sits in a folder with version history behind it. Sessions have
landed work in the other one by mistake, so always spell the OneDrive path out
in a command rather than assuming the shell's working directory.

**Do not read staleness out of an ahead/behind count.** An earlier version of
this note recorded the OneDrive checkout as "sixteen files and 1,834 lines
behind", and that was an artifact, not a fact — it was sitting on a feature
branch whose `main` had simply never been fast-forwarded, and every file the
note called missing was in fact present. Ahead/behind compares two refs, not two
working trees, and it means nothing about currency when the refs are a feature
branch and someone else's `main`. It is the same trap as the `[ahead 113,
behind 1]` reading below. Check `git log` and the actual files before believing
either.

If you end up in that second checkout anyway, know that **its `.venv` is nearly
empty**: the suite reads as broken when it is merely uninstalled, which sends you
hunting a bug that does not exist. Install first, then run tests through the venv's
own interpreter rather than a bare `pytest`:

```
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest tests/ -q
```

If teardown throws `PermissionError` on `pytest-current`, add
`--basetemp="$TEMP/pwbtest"`. That is Windows symlink cleanup, not a test failure —
worth saying because the traceback looks exactly like one.

Separately, `.claude/settings.local.json` regrows dead permission entries on its
own, so cleaning it is never a one-time fix. That is Claude Code behaviour rather
than a fact about this repository, so it lives in the `gexio-machine` skill along
with the fix — not restated here, for the same reason the NEEDS YOU rules are not.

**`jay` and `upstream` now mean the same thing in both checkouts. `origin` does
not.** As of 2026-08-18, both directories have `jay` = the fork
(`jay79-boop/pwb-toolbox`) and `upstream` = the upstream project
(`paperswithbacktest/pwb-toolbox`). `origin` is the one that still differs: it is
**upstream** in the OneDrive checkout and **the fork** in the other, so a bare
`git pull origin main` means two different things depending on which directory
the shell happens to be in, and fails by succeeding against the wrong project
rather than by erroring.

**Never hand over a `merge` or `checkout` without the `fetch` on the same line.**
Both fail by succeeding. `git checkout <branch>` on a branch that already exists
locally is a no-op that reports "Already on ..." and brings nothing down;
`git merge --ff-only jay/<branch>` merges the remote-tracking ref *as of the last
fetch*, so it happily fast-forwards to a commit that is already stale. Each cost a
round trip on 2026-08-22, and in both cases the terminal said what had happened —
"Your branch is behind ... by 2 commits" — while the next step failed with an
unrelated-looking error about a missing file. Write
`git fetch jay <branch>; git merge --ff-only jay/<branch>` as one line, every time,
and end it with a `Test-Path` on a file the new commit adds so success is visible.

**So use `jay` and `upstream` explicitly and never write a bare `origin`
command.** `git fetch jay <branch>` now works identically in both — which was not
true before: the second checkout had no `jay` remote at all, so the command this
file documented silently failed there. It also had no route to upstream, which is
why the OneDrive checkout was the only one that could open an upstream PR.

Check what `main` tracks before reading anything into `git status`. Where it
tracks `origin` and `origin` is upstream, the ahead/behind counts measure the
fork against upstream and say nothing about whether the checkout is current — the
`[ahead 113, behind 1]` seen on 2026-08-18 is that reading, not a health report.

Running only `pwb_toolbox.scraping` and `pwb_toolbox.converting` needs six
packages, not all of `requirements-dev.txt` (which drags in `transformers`,
`datasets`, `scikit-learn`, `scipy`, `matplotlib`, `ccxt` and `ib_insync`):

```
backtrader pandas pytest requests beautifulsoup4 click
```

Verified on Python 3.12 with pandas 3.0.5 — `backtrader` 1.9.78.123 is a 2019
release but runs clean there.

## Design tooling (UI/UX)

`.claude/skills/` vendors the MIT-licensed
[ui-ux-pro-max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) suite,
installed with `npx ui-ux-pro-max-cli init --ai claude`. It is unrelated to the
trading library — the package itself is headless — and exists only so sessions in
this repo can build dashboards, docs pages, and report UIs to a consistent
standard. Nothing under `pwb_toolbox/` imports it, and `pytest` never touches it.

The core skill is a local CSV database (84 UI styles, 192 color palettes, 74 font
pairings, 98 UX guidelines, 25 chart types, 22 stacks) queried with stdlib Python
— no network, no API key:

```bash
python3 .claude/skills/ui-ux-pro-max/scripts/search.py "saas landing page" --domain style
python3 .claude/skills/ui-ux-pro-max/scripts/search.py "fintech dashboard" --domain color --json
```

The SKILL.md frontmatter says "67 styles, 161 palettes" — that string is hardcoded
in the upstream template and lags the shipped CSVs. Trust the data files.

`docs/index.html` is a landing page built entirely from those queries — palette,
font pairing and motion timings all came from the skill rather than being invented.
It is a single self-contained file: both typefaces are embedded as base64 woff2, so
it opens from `file://` with no build step and no network, which is also why it is
~120 KB. Rebuilding means re-querying the skill, not editing the base64 by hand.

`docs/` is published straight from the branch — **Settings → Pages → Source →
"Deploy from a branch"**, branch `main`, folder `/docs`. GitHub builds and serves it
itself; no workflow is involved, and the build shows up in Actions as a
`pages build and deployment` run that nothing in this repo authors.

That is what makes `docs/.nojekyll` load-bearing rather than decorative. The branch
source runs the folder through Jekyll, which would otherwise try to build the `.md`
files sitting beside `index.html`. The file is empty — its presence is the entire
signal.

There used to be a `.github/workflows/pages.yml` that deployed the same directory
through `actions/deploy-pages`. It never once succeeded, and it was removed rather
than left to add a red X to every push touching `docs/`. Two reasons, either
sufficient: a workflow cannot enable Pages here in the first place, because the
default `GITHUB_TOKEN` may not create a Pages site (`Create Pages site failed.
Error: Resource not accessible by integration` — `pages: write` covers deploying to
a site that already exists, not creating one); and once a branch source is selected,
`actions/deploy-pages` cannot target it at all, since it only deploys to sites whose
build type is `workflow`. Reviving it would mean switching Source back to "GitHub
Actions" and enabling Pages by hand first.

The installer also drops six companion skills (`design`, `design-system`,
`ui-styling`, `brand`, `slides`, `banner-design`) alongside the main one. They were
removed deliberately — several of their generators shell out to `npx shadcn` or
image APIs and none were needed here. Re-running `uipro init` restores them, so
prune again after any upgrade.

`.mcp.json` registers 21st.dev's [21st MCP](https://21st.dev/mcp) (the successor to
Magic MCP) for generating React/Tailwind components. It is an HTTP server
authenticated with `${API_KEY_21ST}` — never hardcode the key in `.mcp.json`.

Claude Code expands `${...}` from its own process environment and does not read
`.env`, so how you supply the key depends on where the session runs.

**Local sessions** — export it before launching, from your shell profile or with
`set -a; . .env; set +a`:

```bash
export API_KEY_21ST=...   # https://21st.dev/settings/api-keys
```

**Claude Code on the web** — there is no shell profile to export from, so the key
goes in the cloud environment's **Environment variables** field, in the environment
dialog at claude.ai/code (opened with the cloud icon; personal environments have no
separate page in account settings). The field takes `.env` format, one `KEY=value`
per line:

```text
API_KEY_21ST=...
```

Sessions copy those values into their process environment once at startup, which is
what `${API_KEY_21ST}` then expands from. Editing the field only affects sessions
started afterward — a running session keeps the values it booted with, so start a
new one rather than expecting a live pickup.

Two things bite on the web beyond the variable itself:

- **Network access.** The server dials `https://21st.dev/api/mcp` from the session's
  own network. The default **Trusted** level allows package registries, GitHub, and
  cloud SDKs — not 21st.dev — so the environment needs **Custom** access with
  `21st.dev` in **Allowed domains**, and "Also include default list of common package
  managers" ticked to keep the Trusted set. The exemption that lets MCP *connectors*
  skip the allowlist does not apply here: it covers claude.ai connectors routed
  through Anthropic's servers, not a project `.mcp.json` server.
- **Visibility.** Cloud environments have no secrets store, and Anthropic's docs
  advise against putting API keys in environment variables at all — anyone who uses
  the environment can read them, and an org-shared environment exposes them to every
  member. A scoped 21st.dev key is a low-stakes thing to accept that for, but treat
  it as a deliberate trade: keep it in a personal environment, not a shared one, and
  rotate it at https://21st.dev/settings/api-keys if it leaks.

Without the variable the server fails to authenticate; nothing else in the repo
is affected. Note that `21st` authenticates by header, not OAuth — picking it from
the `/mcp` menu and signing in through a browser will not fix a missing key.

## Credentials

`load_dataset` reads `PWB_API_KEY`, falling back to the Hugging Face Hub and
then to yfinance. Never commit keys; `.env` is gitignored.

`API_KEY_21ST` (21st.dev, from https://21st.dev/settings/api-keys) is read from the
environment by `.mcp.json`. Never commit keys; `.env` is gitignored.

`.env.example` lists both variables. Copy it to `.env` and fill it in — but note
that `.env` alone does not reach `.mcp.json`, which reads the process environment.
Locally that means exporting the key; on the web it means setting it in the cloud
environment's variables. Both routes, and the network and visibility caveats that
come with the web one, are under "Design tooling (UI/UX)" above.

---

# The ledger

Project state lives in three places, split by how fast each kind of fact goes
stale. This file is the slow half: what the project is, how to work in it, and
the traps that cost real days. It is loaded into every session automatically,
so keep it durable and keep it short.

- **`docs/state.md`** — operating state: the fleet registry, the roadmap, the
  tech stack, and the honesty lessons. Hand-maintained, changes on the order of
  weeks. Not auto-loaded; read it when you need it.
- **`docs/decisions/`** — one file per decision, newest first in
  `docs/decisions/README.md`. Append a new file for a new decision; never
  rewrite an old one. A correction is a new entry that supersedes.
- **Nowhere at all** — open pull requests, their CI, what `main` points at, and
  any count of them. **Derive those from git and the GitHub tools at read
  time.** They were written down for months and were wrong within hours every
  time; three separate sessions wrote a wrong PR count on 2026-08-24 alone. The
  SessionStart hook already tells every session to gather them fresh.

If you are about to write a PR number, a commit SHA, or "N open" into this file
or into `docs/state.md`, that is the mistake the split exists to prevent.

**Why it was split.** Every branch doing real work also edited one dense region
of prose here, so branches conflicted on `CLAUDE.md` and on nothing else — and
on 2026-08-24 one branch merged with *zero* conflicts while silently reverting
the whole region to a state three merges stale. A conflict at least stops you
and demands a decision; a clean merge of two contradictory claims just picks
one. Per-file decisions removed the shared insertion point, and deriving the
volatile facts removed the thing worth fighting over.
