# Work order: trim CLAUDE.md

**Delete this file once the trim has merged.** It is a work order, not a doc.

Written 2026-09-03 from inside a session that had hit its own hard stop, so the
analysis is done and the next session only has to execute. Do not re-derive it.

## The finding

`CLAUDE.md` is **32,772 characters, about 8,193 tokens**, and it is loaded into
every session in this repository. Measured the same day: a session's first turn
cost 83,853 tokens before anything was asked, and that whole fixed block is
re-read on every subsequent turn. Across an 87-turn session that was 6.9M of
12.5M tokens — 55% of everything the session spent — paid for scaffolding rather
than work. CLAUDE.md is roughly a tenth of that block, on every turn of every
session, forever.

The file's own second line says: *"This file is loaded into every session, so it
is kept short on purpose."* It has failed its own stated goal by a wide margin.
That sentence is the mandate for this change.

**Target: under 2,500 tokens (~10,000 characters). A ~70% cut.**

## The principle that decides every cut

CLAUDE.md is the *always-loaded* tier. A fact belongs in it only if a session
that has not yet been told what it is working on would be damaged by not knowing
it. Everything a session needs only while doing a particular job belongs in a
doc or skill that loads on demand.

Applied: **a trap that costs a day if unknown at minute zero stays. Narrative
explaining how a trap was discovered goes.** The reasoning is not lost — it moves
to the doc that already exists for it.

## The cuts, in order of tokens returned

Almost every long section here already names the file that holds its detail, so
most of this is deleting prose that duplicates a document it is already pointing
at. That makes it low-risk. Verify each target file exists before cutting into
it; if one does not, create it in the same PR rather than dropping the content.

| Section in CLAUDE.md | Action | Destination (exists already) |
| --- | --- | --- |
| "Brainstorm before building" | move, keep 2 lines + link | `.claude/skills/gexio-machine` — the section itself says the cross-project copy belongs there |
| "Do the work. Hand back only what genuinely needs them" | move, keep 3 lines + link | `gexio-machine` skill |
| "Do not use the user as a test harness" | delete, keep 1 line + link | `gexio-machine` skill, `docs/converter-corpus.md` |
| "Flagging action items" | keep the ledger URL + append rule; move the rest | `gexio-machine` skill |
| "Commands" (the long code block) | keep ~6 commands; move the rest | `docs/layout.md` — already says it "lists the rest" |
| "The desk agent runs itself" | keep 3 lines + link | `tools/desk_agent/README.md`, `docs/tradingview-agent-security.md` |
| "Situational awareness" | keep 2 lines + link | `docs/awareness.md` |
| "Backtesting: two things..." | keep the 2 trap names + link | `docs/backtesting.md`, `backtest-trust` skill |
| "The user's local checkout" | keep the OneDrive-is-canonical line + link | `docs/local-checkout.md` |
| "Nor is the Obsidian vault" | keep "never push to the vault" + link | `docs/vault-route.md`, `vault-route` skill |
| "Design tooling, and credentials" | keep "never commit keys" + link | `docs/design-tooling.md` |
| "Environment" | keep venv + PYTHONPATH; move CI detail | new `docs/environment.md` |
| "Where the work happens" | compress to ~5 lines | `docs/local-checkout.md`, `gexio-machine` skill |

## What must NOT be cut

These are load-bearing at minute zero of a session that has been told nothing.
Cutting any of them re-opens an incident this repo already paid for:

- **Run the `gexio-machine` skill before writing any command for the owner.**
  Their shell is PowerShell 5.1; bash-shaped commands fail confusingly.
- **The local-vs-cloud capability gap.** Getting it wrong cost two days once.
- **The Action Ledger URL, and "append, never start a second one."** It is the
  only channel through which one session can see what another is doing.
- **`black pwb_toolbox/ tools/ tests/` — never bare `black .`.** It would rewrite
  `pwb_toolbox_legacy/` and the vendored skill.
- **Do not pin a test count in this file.** Every branch adding a test would
  collide on that line.
- **Tests must not need network or a live broker.**
- **`C:\Users\Gexio\OneDrive\pwb-toolbox` is the canonical checkout**, and
  `origin` means different things in the two clones.
- **Never push to the vault** — it breaks the owner's nightly backup.
- **The `## 🔴 NEEDS YOU` block ends every reply.**
- **The ledger split at the bottom** — what is derived at read time vs written
  down. That section is the reason the file does not carry stale PR numbers.

## The risk, and how to handle it

CLAUDE.md's own history is the warning: every branch doing real work used to edit
one dense region of it, so branches conflicted there and nowhere else — and on
2026-08-24 one branch merged with **zero** conflicts while silently reverting the
whole region to a state three merges stale.

So: **one focused PR, nothing else in it, landed fast.** Before starting, check
for other open PRs touching CLAUDE.md and land or close them first. Do not
combine this with any other change.

## Done looks like

1. `wc -c CLAUDE.md` under ~10,000.
2. Every "must not be cut" item above still present, verifiable by grep.
3. Every moved section's destination file actually contains the moved content —
   check by reading the destination, not by trusting the move.
4. `pytest tests/ -q` passes and `black --check pwb_toolbox/ tools/ tests/` is
   clean.
5. A decision record in `docs/decisions/` dated the day it lands, naming the
   before/after size and the principle above, linked from
   `docs/decisions/README.md`.
6. This brief deleted in the same PR.
