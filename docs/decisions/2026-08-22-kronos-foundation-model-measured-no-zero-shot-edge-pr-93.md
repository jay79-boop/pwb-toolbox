# Kronos foundation model: measured, no zero-shot edge (PR #93)

*Decided 2026-08-22.*
**Decision:** Before integrating the Kronos K-line foundation model
(shiyu-coder/Kronos) anywhere, measure it with `tools/kronos_lab.py`. Result on
Kronos-small, zero-shot, 60 non-overlapping 12-bar windows of hourly bars, all
post-training-cutoff 2026 data: BTC-USD 46.7% direction hit rate (p=0.70),
ES=F 51.7% (p=0.90), information coefficients ≈ 0 on both, path error worse
than persistence on both.
**Why:** Three candidate uses were on the table — a fourth signal for the
backtest lab, a confirmation filter on ICT entries, a discretionary forecast
chart. All three require measurable directional skill; none was found.
**Outcome:** Kronos stays out of the backtest lab, the desk agent, and live
decisions. The forecast-chart mode exists but must not inform trades. The lab
tool stays merged as the standing instrument for any future revisit (a
fine-tuned variant, a newer model release) — re-run the eval before believing
any of them.
