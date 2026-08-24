# ICT AM/OB Strategy Refactor

*Decided 2026-08-10.*
**Decision:** Hoist computed security reads instead of subscripting floats in `next()`.
**Why:** Cleaner data flow, reduces session state bugs.
**Outcome:** Merged PR #76. Reduced float handling surface area.
