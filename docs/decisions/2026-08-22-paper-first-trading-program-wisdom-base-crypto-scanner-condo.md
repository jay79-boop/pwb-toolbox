# Paper-first trading program: wisdom base + crypto scanner + condor mock run

*Decided 2026-08-22.*
**Decision:** Start a paper-first trading program with hard gates to live
money. Three pieces shipped together: `docs/trading-wisdom.md` (ten sourced,
machine-enforceable risk rules and the evidence base), `tools/crypto_scan.py`
(the "trade crypto" screener over evidence-backed momentum signals), and a
weekly SPX/XSP iron condor mock run starting Monday (paper only, collecting
expected-move-vs-realized data).
**Why:** The owner wants to trade actively without breaking the bank. The
published base rates (97% of persistent Brazilian day traders lost money;
~1.6% of Taiwanese day traders predictably profitable) say unstructured
retail trading fails by default; the documented practices of successful
traders converge on small constant risk, positive expectancy proven before
sizing up, and mechanically enforced limits.
**Gates to live:** a strategy trades real money only after positive
expectancy over ≥30 paper trades (rule 9 in the wisdom doc), and then under
the desk agent's caps (PR #78). "Self-learning" = the propose-then-approve
loop in the wisdom doc: the system drafts its own rule changes from journal
evidence; the owner approves every change.
**Also decided:** Kronos fine-tuning parked — not until the backtest lab
(#87) is merged and the paper program is producing data; any future
fine-tuned model must pass `kronos_lab` eval before use.
