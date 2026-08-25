# Layout

The full inventory of what lives where. `CLAUDE.md` keeps a short map of the
top-level directories and points here for the detail.


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
- `tools/ai_company.py` — the one-person AI company, made checkable. Its
  subject is `docs/blueprint-one-person-ai-company.json`, a whole local service
  business as a blueprint — five loop stages, every process, every step's
  executor named — and the doctrine is `docs/one-person-ai-company.md`. Derives
  the agent roster **from the map** rather than keeping a list beside it, so
  the two cannot disagree; convicts any AI step that commits money with no
  person step in front of it (`commits: true` on a step is the declaration —
  reading a payment system and charging through one are the same tool, so it
  cannot be inferred); prices the person steps; runs the loop economics; and
  gates repricing on sample size before drift, so "not enough jobs yet" is a
  first-class answer. `page` regenerates `docs/one-person-ai-company.html` from
  the blueprint — edit the blueprint, never that file. It is the reference
  target for phase 7 of the readiness framework, stamped into an engagement by
  `engagement.py seed-target`
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
- `tools/strategy_lab/` — live dashboard for strategy test runs; stdlib-only server
  serving `static/strategy-lab.html` plus a run API. Every strategy test in this repo
  reports into it through one JSON run-record contract, so results accumulate and
  compare instead of scrolling past in a terminal. `--export` writes a standalone
  snapshot for publishing or reading off the machine
- `static/strategy-lab.html` / `static/strategy-lab-stats.js` — the Strategy Lab
  dashboard and its arithmetic. Same arrangement as `option-lab.js` and the journal:
  the stats module is a separate, tested file that the page inlines verbatim, and
  `tests/test_strategy_lab.py` requires its numbers to agree with
  `pwb_toolbox.performance.trade_stats` so the screen and the package cannot drift
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
  `check` alerts when an open trade's stop or target level trades. `open
  --place` sends an option plan to the IB **paper** account after the caps have
  accepted it — the ordering is what makes "no log, no trade" a property rather
  than a convention, and the flag refuses any non-paper port. Protocol
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
  (`tests/test_spend_watch.py`); protocol in `docs/spend-safety.md`. Also flags
  two enabled Routines running the same job on the same cron, and its `session`
  command warns — from the session's own transcript, costing no tokens — when
  the current session has itself grown expensive to keep going. Wired to every
  prompt by `.claude/hooks/session-size.sh`
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
- `tools/broker_costs.py` — prices the same option structure at every broker on
  the shortlist, with platform fees in the total where they belong, because a
  headline commission decides nothing on its own. `condor` runs the weekly
  SPX/XSP program a year at a time across a size sweep; `spread` reports best
  against worst, which is the only number that says whether a difference is
  real. The finding it exists to produce: at one lot the entire spread is under
  $200/yr, so broker choice here is a capability question and not a cost one —
  pinned by a test, so it fails loudly if that stops being true
- `docs/brokers.md` — which broker to execute through, scored against what the
  desk actually trades rather than against a rate card. Ten-broker shortlist
  with sourced fees and API terms, the incumbent setup (IB, Schwab, TradingView,
  CCXT) it has to beat, and the finding that decides it: Schwab's Trader API is
  live-only and cannot drive paperMoney, so the one account already holding the
  options is the one that cannot fill rule 9's paper record
- `docs/` — `datasets.md`, `backtesting.md`, `execution.md`, `scraping.md`, `converting.md`,
  `ai-readiness-framework.md` (the engagement playbook `tools/engagement.py` tracks),
  `page-style.md` (the standing look for any page or artifact built here —
  light, colour-coded, validated; the rule is in `CLAUDE.md`, the tokens and
  the checks are here),
  `one-person-ai-company.md` (the reference target architecture that playbook's
  phase 7 designs toward — a local service business as a loop rather than a
  funnel, with agents on the information and people on money and risk), plus
  `index.html` (the published landing page; see "Design tooling" below),
  `tradingview-mcp.md` (connecting Claude to TradingView Desktop over the Chrome
  DevTools Protocol — unrelated to the library, written down because the setup has
  traps that otherwise get rediscovered every time),
  `tradingview-agent-security.md` (whether to point an agent at TradingView at all,
  and on which account — the CDP threat model, the two-login rule, and what was
  actually verified about the open-source bridge by reading its source),
  `agent-fleet.md` (critique and design of the owner's multi-agent fleet — the
  operating procedure itself is the `agent-fleet` skill under `.claude/skills/`),
  `skills.md` (the bar for turning a repeated job into a skill, the two homes a
  skill can live in, and the retirement rule — with `prompts/` as its staging
  area for long prompts not yet packaged),
  and the spend-safety pair — `token-drain-2026-08-24.md` (what exhausted a
  five-hour window, measured rather than guessed) and `spend-safety.md` (every
  surface that can reach a card, ranked by worst case, and the five layers that
  bound them; the rules themselves are the `spend-safety` skill)

