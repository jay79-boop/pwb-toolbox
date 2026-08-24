# Stream intel: costs closed, prop evals priced

*Decided 2026-08-24.*
**Decision:** A live stream's backtest-hygiene argument was distilled into
`docs/field-notes-prop-firms-and-data.md` and acted on the same day. Four
changes: `reversal_15m_sim` now charges costs by default (1bp round trip,
`--no-costs` to compare) with the export bridge netting the friction so the
night lab's R stays consistent; the sim reports profit factor, Sortino, max
drawdown in R, and a chronological first/second-half split; `--fragility-out`
sweeps rr and sma-length into night-lab fragility specs that a bare `plan`
picks up by file convention; and `tools/prop_sim.py` prices prop-firm
evaluations (the stream's one genuinely new idea).
**The catch that mattered:** the sim shipped frictionless despite this
repo's own "charge costs always" doctrine, and its frictionless trades were
feeding the night lab's stress record. A stranger's checklist found it.
**The prop math worth remembering:** for a pure symmetric walk, P(pass) =
D/(T+D) regardless of position size — pinned against the closed form.
Sizing moves pass odds only through the rules (time limits reward it,
consistency rules tax it, trailing drawdowns are strictly worse than
fixed), and on the demo rule set a coin flip is EV-negative at every size.
The tool exists so any real firm's rules get priced before an eval is
bought; owner's stated intent is "want the math first", not a commitment.
