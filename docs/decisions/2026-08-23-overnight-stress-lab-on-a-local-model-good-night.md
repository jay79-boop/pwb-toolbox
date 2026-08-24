# Overnight stress lab on a local model ("good night")

*Decided 2026-08-23.*
**Decision:** Add `tools/night_lab.py` — a 1am-8am unattended stress lab
driven by a local Ollama model, triggered by saying "good night". Four job
kinds: adversarial attacks on open theses, shock scenarios over the closed
record, parameter fragility sweeps, and leak-mining the paper history.
**The rule:** *the model proposes, Python computes.* An LLM cannot calculate
a drawdown; asked to, it produces a fluent unfalsifiable number, and seven
hours of that is seven hours of fiction that looks like analysis. So the
model only generates hypotheses — the thing it is good at and that gets
better with volume you could not afford to buy at cloud prices — and every
resulting figure comes from deterministic arithmetic over the real ledger.
Proposals that will not parse, name no checkable condition, or drift onto a
ticker the trade does not hold are **dropped, never repaired**.
**Why overnight at all:** the arithmetic alone runs in seconds and does not
need the night. What needs the night is *volume* of hypotheses, which is
exactly what a free local model buys.
**Yielding:** the window and the idle timer are a pure function
(`next_action`), so 3am behaviour is tested at 3pm. Jobs are small and the
queue checkpoints after each one, so a person sitting down at 3am costs the
job in flight and nothing else. `keep_alive: 0` hands the GPU straight back.
**Morning:** silence is the good outcome — `verdict --quiet` prints nothing
when nothing broke, and the orientation hook stays quiet with it. Findings
stage as **pending** proposals under the wisdom doc's propose-then-approve
contract; the lab never changes a rule or places a trade.
