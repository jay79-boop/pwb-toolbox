# T-Bill Ladder Offline-First Design

*Decided 2026-08-01.*
**Decision:** Accept `--rate` overrides instead of calling Treasury when live data blocked.
**Why:** Cloud containers can't reach home.treasury.gov; need to work offline.
**Outcome:** Merged PR #68. Math still validates without live data.
