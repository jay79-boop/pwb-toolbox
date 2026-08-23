# The night lab

Unattended trade stress testing between 1am and 8am, run by a local model on
your own machine. You say **"good night"**; it grinds until the window closes
or you touch the keyboard; a verdict is waiting at breakfast.

## The rule that makes it worth running

> **The model proposes. Python computes.**

This is the whole design, and it is worth being blunt about why. A local LLM
cannot calculate a drawdown. Ask one what a strategy does under a vol shock
and it produces a fluent, confident, unfalsifiable number — and seven hours
of that is seven hours of fiction that looks exactly like analysis.

Meanwhile the actual arithmetic — Monte Carlo over trade order, gap and
slippage shocks, correlation breaks, parameter sweeps — runs in *seconds*.
It does not need the night.

So the night is spent on the one thing a model is genuinely good at and that
gets better with volume you could not afford to buy: **generating hypotheses**.
Every hypothesis is then parsed into a structured spec, clamped to sane
bounds, and executed by deterministic arithmetic over your real record.

A proposal that will not parse, or that names nothing checkable, is
**dropped rather than repaired**. A dropped job is a fine outcome. An
unverifiable finding in front of you at breakfast is not.

## The four job kinds

| Kind | The model's job | Python's job |
| --- | --- | --- |
| `redteam` | Attack an open thesis | Keep only attacks naming a symbol, comparison, level and deadline |
| `shock` | Author a scenario spec | Compute every resulting number from the closed record |
| `fragility` | *nothing* | Sweep a parameter, report the drop one step either side |
| `leaks` | Propose a pattern in the record | Test it: effect size, sample size, does it hold |

### redteam — an attack you cannot check is not an attack

"It could go down" is not an attack, it is a description of trading. What
earns a place in the morning report is a condition tomorrow's market either
satisfies or does not:

```json
{"claim": "breakout unconfirmed on volume", "severity": "high",
 "falsifier": {"symbol": "NVDA", "op": ">=", "level": 182.0, "by": "2026-08-29"}}
```

Attacks are also checked against the trade's **own ticker**. A local model
will happily attack your TSLA thesis with an NVDA level; that gets dropped.
Macro symbols (SPY, VIX, DXY, BTC-USD and friends) are allowed through,
because "this only works while SPY holds up" is real reasoning.

Near-identical attacks collapse: fifty rephrasings of one idea is one
finding, and dedupe keys on the falsifier rather than the wording.

### shock — the model picks the scenario, the arithmetic is ours

The model proposes `gap_pct`, `loss_mult`, `win_mult`, `slippage_bps`,
`corr_to_one` and `resample`. Every one is clamped: it may be dramatic about
*which* stress to apply, not invent a 90% gap that makes every strategy look
equally doomed.

Note `win_mult` defaults to **1.0** and is bounded at 1.5. A vol spike does
not improve your exits, and letting the model widen winners would flatter
the result. The asymmetry between `loss_mult` and `win_mult` is the stress
assumption, and it is named in the field rather than hidden inside a
"volatility" knob.

`resample` bootstraps the trade *order*. This is the one that tends to hurt:
a record that survived only because a big winner happened to land before a
losing streak has not been tested, it has been remembered.

### fragility — a lone peak is a fitted parameter

No model involved at all. Given a sweep and the value you chose, it reports
how much of the metric you lose by being one step off. A robust setting sits
on a plateau. A fitted one is the lone spike of its own sweep, and the drop
to the next setting along is the tell.

### leaks — the model proposes, the record decides

The model is good at noticing "these all seem to be short-dte Fridays" and
bad at knowing whether it is true. So it supplies the filter and Python
supplies the verdict: the group's mean R against everything else, the sample
size, and whether the claimed direction holds. Anything under five trades is
dropped — with four trades you can find any pattern you like.

## Feeding it backtests, not just the desk

The lab was pointed at the paper desk, but a new desk is empty and stays
thin for weeks. Backtests are the other record worth stressing — arguably
the more important one, since stress-testing a strategy *before* it risks
paper money is the whole point of the paper-first gate.

`reversal_15m_sim.py --trades-out trades.json` exports its closed trades in
the lab's shape, and `night_lab.py plan --sim trades.json` merges them into
the night's record (repeat `--sim` for several files). Sim trades join the
closed record only: they can be shocked, resampled and mined, but they carry
no thesis, so they are never red-teamed. They arrive tagged `lane: sim-15m`,
which keeps leak-mining honest about what is a sim result and what is a real
paper trade.

`plan` snapshots the merged record to `night_lab/record.json`, and `run`
computes from that snapshot — so the 1am grind works on exactly the state
that was armed at "good night", not on whatever the ledger looks like by
morning.

Getting bars for the sim: `reversal_15m_sim.py bars.csv --fetch "ES=F"`
pulls up to ~59 days of 15-minute bars from Yahoo into the CSV first (needs
`yfinance`, runs on your machine — the cloud proxy blocks Yahoo). ES=F
matters: the default 09:15 ET candle exists only in the electronic session,
which regular-hours SPY bars do not carry.

## The window, and yielding to you

Jobs are small and the queue is checkpointed after every one, so being
interrupted costs at most the job in flight.

- **1am–8am** by default (`--start-hour`, `--end-hour`; a window that wraps
  midnight works too).
- **Yields when you are at the keyboard.** Idle time comes from
  `GetLastInputInfo` — real user presence, not CPU load, because a busy
  machine with nobody at it is exactly when the lab should be working.
  Two minutes of quiet and it takes the machine back.
- **Stops at 8am** whatever is left. Unfinished jobs stay queued for
  tomorrow; nothing is lost and nothing is rushed.
- **Unloads the model on every call** (`keep_alive: 0`), so a yield hands
  your GPU straight back instead of holding VRAM all night.

`next_action(hour, idle_seconds)` is a pure function, which is why the 3am
behaviour is tested at 3pm rather than observed once and hoped about.

If a job raises, it is recorded as failed and the runner moves on. The catch
is deliberately broad: this runs unattended with nobody to restart it, so an
escaping exception costs every remaining hour of the window, not one job.

## The morning

**Silence is the good outcome.** `verdict --quiet` prints nothing when
nothing broke — a night that found nothing should not report that it found
nothing, which is noise dressed as diligence.

When something did break you get one screen, worst-first, with scenarios
collapsed:

```
Night lab (2026-08-23) — 8 job(s):
  BROKE  worst of 4: scenario-11 — drawdown 48.61R vs 10.0R pot (+3 more)
  RISK   scenario-13: 58% of resampled orderings empty the pot
  THESIS NVDA 02OCT26 190C: 1 falsifiable high-severity attack(s)
         check NVDA >= 182.0 by 2026-08-29
  LEAK   short-dte bleeds (n=8, -0.45R vs the rest)
```

The full write-up lands in `night_lab/report-YYYY-MM-DD.md` for when you want
to know why something was flagged.

Findings that want a rule change are staged in `night_lab/proposals.jsonl` as
**pending** — same contract as `docs/trading-wisdom.md`: the system drafts,
you approve. Nothing in `night_lab/` changes how anything trades on its own.

## Commands

```bash
python tools/night_lab.py plan          # build tonight's queue from the desk
python tools/night_lab.py run           # the overnight grind
python tools/night_lab.py run --once    # a single job, for a smoke test
python tools/night_lab.py run --now-anyway   # ignore window and idle timer
python tools/night_lab.py status        # what is queued and what has run
python tools/night_lab.py verdict       # the morning one-screen
python tools/night_lab.py verdict --quiet    # silent unless something broke
```

`--now-anyway` is the smoke test: it forces the window open and ignores the
idle timer, so you can prove a run works at 3pm while watching it, rather
than discovering at 8am that it never started.

## Choosing a model

Seven hours buys wildly different amounts of work depending on hardware, and
the right model is the biggest one that still finishes a job in about a
minute:

| Situation | Model | Roughly |
| --- | --- | --- |
| 24GB+ VRAM | `qwen2.5:32b` or `llama3.1:70b` (quantized) | best hypotheses |
| 8–16GB VRAM | `llama3.1:8b`, `qwen2.5:14b` | the sweet spot |
| CPU only | `llama3.2:3b` | few hundred jobs a night; keep the queue short |

Quality matters less here than it would elsewhere, because the filters do not
care how eloquent a proposal was — only whether it survives being checked.
A smaller model proposes more junk; the junk gets dropped, and the volume
still finds things.

## What this is not

It is not a backtester and it does not trade. It reads your desk ledger and
writes to `night_lab/` only. Every gate in `docs/trading-wisdom.md` and
`docs/spec-desk.md` still applies to anything it surfaces — a finding here is
a reason to look, never a reason to act before the pre-trade pack.
