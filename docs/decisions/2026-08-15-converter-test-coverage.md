# Converter Test Coverage

*Decided 2026-08-15.*
**Decision:** Test `pwb_toolbox.converting` by compiling generated Backtrader code + running on synthetic bars, not just parsing.
**Why:** A converter that parses but doesn't execute is a failure waiting to happen.
**Outcome:** Tests in `tests/test_converting.py` end-to-end section now validate execution.
