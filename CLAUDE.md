# pwb-toolbox

A toolbox library for quant traders: datasets, backtesting (Backtrader), live
execution, and performance analytics. Requires Python 3.10+.

**This file is read into every session, so it costs tokens on every turn.** It
carries rules and traps only — one line each. The reasoning, the incident
narratives and the worked examples live in `docs/` and are read on demand.
The full pre-2026-09-04 text is preserved at
`docs/claude-md-archive-2026-09-04.md`; read it when you need to know *why*.

## Working with the owner

- **Local vs cloud is load-bearing.** A local session (their Windows machine)
  can touch `C:\Users\Gexio\...`, run their venv, open the trade journal. A
  cloud session shares only GitHub — no disk, no scheduled tasks. Both read this
  file, which is why they get confused. Getting it wrong cost two days once.
- **Run the `gexio-machine` skill before writing a single command for them.**
  It carries the PowerShell traps, the machine facts, and the NEEDS YOU format.
- **Brainstorm before building.** For anything beyond a small correction, put
  trade-offs to them via `AskUserQuestion` (tick, don't type), lead with a
  recommendation, use `multiSelect` freely. Push back on a flawed framing.
- **Do the work; hand back only what genuinely needs them.** Before anything
  goes in a NEEDS YOU block: *is there any route by which this session could do
  it?* If yes, do it. Only these are theirs: a credential or answer only they
  hold; a GUI action this session cannot reach; a command that must run on their
  Windows machine while the session is in the cloud; a decision that changes
  money, scope, or something hard to reverse. "Run this and tell me what it
  says" is almost always a failure to try it here first. Batch what is left.

## Flagging action items

One `## 🔴 NEEDS YOU` block at the very end of the reply, never buried. Format
rules live in the `gexio-machine` skill. Rules that are *not* in the skill:

- **Every item is a markdown checkbox `- [ ]`** — never a bullet or number.
- **Never indent prose under a checkbox.** Four-space indent renders as a code
  block, which reads as "paste this" — and they have pasted explanation text
  into PowerShell. An indented block means pasteable and nothing else.
- **Switching programs is its own step, with its own success check.** Naming the
  program inside the same step does not move anyone out of the window already in
  front of them — a Claude Code prompt handed over that way went into PowerShell
  and its parentheses parsed as a subexpression. One step opens the program and
  states what the new screen looks like; the *next* step is the paste.
  `docs/decisions/2026-09-04-naming-the-program-does-not-move-anyone-out-of-the-window.md`
- **Point at the exact place**, never at where to look for it: give the URL that
  lands on the thing, filtered to it, then name the control to click.
- **A step explained in prose above still gets repeated in the block.**
- **The block is also copied into the Action Ledger**, and that copy is the one
  that survives: <https://claude.ai/code/artifact/a9da0f16-1b7f-4658-a21f-70271be5c413>
  Append; never start a second one. Read it with the Artifact tool
  (`action: "read"`), edit the state JSON in `<script id="app-state">`,
  republish with `url` set to that same address.
- **Read the ledger before opening a pull request**, not only when appending —
  it is the only channel through which one session sees what another is doing.
- **Tick every row you can confirm is done, including theirs**, in the same turn,
  and say what confirmed it. The bar is *verified* — a test run, an API read, a
  file checked — never *believed*, and never to tidy the list.

## Layout

- `pwb_toolbox/` — the shipped package (`datasets`, `backtesting`, `execution`,
  `performance`, `scraping`, `converting`, `options`, `journal`, `vision`)
- `pwb_toolbox_legacy/` — superseded, kept for reference; not public API
- `tests/` — pytest suite
- `tools/` — the desk: trade cards, ladders, labs, scanners, the desk agent
- `static/` — single-file browser tools that open from `file://`, no build step
- `pine/` — TradingView strategies as reviewable source; nothing imports them
- `docs/` — manuals, field notes, decision log
- `.claude/skills/` — procedures worth not retyping; `docs/skills.md` is the bar

**`docs/layout.md` is the full inventory.** Read it before building something
that may already exist. **`docs/page-style.md`** is the standing page style
(light, colour-validated, numbers derived) and does not need to be asked for.

## Environment

Dependencies in `requirements.txt`; `requirements-dev.txt` adds `pytest`. CI
(`.github/workflows/tests.yml`) runs two jobs on 3.11: `test` (`pytest tests/
-v`) and `format` (`black --check --diff pwb_toolbox/ tools/ tests/`), separate
so a formatting nit cannot mask a real failure.

`.claude/hooks/session-start.sh` builds `.venv/` **asynchronously** in cloud
sessions — an import failure in the first moments means the install is still
running; re-run rather than treating it as real. No-op locally.
`.claude/hooks/session-size.sh` warns once when the session passes 10M/25M/50M
cache reads. `tools/install_spend_hook.py` installs it into `~/.claude/` so it
fires in every project; `tools/install_global_instructions.py` writes
`docs/global-instructions.md` into the user-level `CLAUDE.md`. **Both are local
only** — a cloud container's `~/.claude` dies with the container.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && export PYTHONPATH="$PWD"
```

Tests import from the repo root. `pythonpath = ["."]` in `pyproject.toml` covers
pytest; the export covers ad-hoc `python -c`.

`.github/workflows/delete-merged-branch.yml` deletes merged head branches
because **ref deletion is the one git operation a cloud container cannot do**
(proxy 403). Needs Workflow permissions set to "Read and write". Trap:
`docs/decisions/2026-08-24-deleting-a-merged-head-branch-orphans-any-pr-stacked-on-it.md`.

## Commands

```bash
pytest tests/ -v                  # full suite (~28s cold / ~15s warm)
black pwb_toolbox/ tools/ tests/  # format; CI checks this exact scope
python tools/spend_watch.py session <transcript>.jsonl  # is this session too big
python tools/awareness.py brief --short   # where things stand, in six lines
python tools/front_door.py build          # rebuild the desk index
```

**`docs/layout.md` lists every other tool with what it is for.** Do not
duplicate that list here — it went stale three times.

## Conventions

- `black` with defaults over `pwb_toolbox/ tools/ tests/` — that exact scope.
  Never bare `black .`: it would rewrite `pwb_toolbox_legacy/` and the vendored
  skill under `.claude/skills/`. The pin in `requirements-dev.txt` is
  deliberate; bump it and reformat in the same commit.
- **Do not pin a test count in this file.** Every branch adding a test would
  touch that line, so any two open PRs collide on it.
- Tests must not need network or a live broker. `ib_insync` runs against a
  mocked `IB`; dataset tests must not need `PWB_API_KEY`; `scraping` uses a fake
  session with injectable `sleep`/`monotonic`.
- `converting` emits Backtrader source, so its tests compile the generated code
  and run it through a real `cerebro`. Parses-but-does-not-trade is a failure.
- Regression tests pin the previous numeric output where old behaviour must hold.
- **Do not use the user as a test harness.** Build a corpus and sweep it. A
  non-zero crash count from `tools/pine_sweep.py` is a converter bug, not a fact
  about the corpus — `convert` is contracted never to raise.
  `docs/converter-corpus.md` has the commands and the proxy limits.

## Traps that cost real days

Each of these has a full account in `docs/`; the one-liner is the rule.

- **Backtests lie twice.** A vendor's timestamps are in whatever zone they chose
  and the file does not say (histdata M1 is New York *with DST*: +39 points read
  wrongly, +7 read correctly). And two vendors of the same instrument gave +50bp
  and −234bp over the same eight years while correlating 0.93. Run
  `verify_timezone` across a winter *and* a summer month, then `noise_floor()`,
  before believing any number. Compare in basis points, and always charge costs.
  `docs/backtesting.md`, `.claude/skills/backtest-trust/`.
- **The desk agent's run log is committed and pushed on purpose** — it is the
  only part of the agent a cloud session can see. The agent may **not** edit its
  own guardrails, `runs.jsonl`, or `runlog.py`. Whether a job needs a desktop is
  a per-job fact in `register_desk_agent.ps1`'s `$jobs` table (`NeedsDesktop`),
  mirrored in `run_job.ps1` and `autologon.ps1`, with a test asserting all three
  agree. **`S4U` is banned for every job** — it carries no credentials, so DPAPI
  secrets and OneDrive paths both fail unattended.
- **The awareness layer stores observations, never state**, and refuses to
  conclude. `docs/awareness.md`.
- **The trade journal is not in this repository.** It lives only at
  `C:\Users\Gexio\OneDrive\trade-journal\`. Ask for the file; do not reconstruct
  it from git history. It inlines `static/option-lab.js` and
  `static/journal-shots.js` verbatim — edit the module here, test, re-inline the
  whole file; never patch the inlined copy.
- **The Obsidian vault is not mirrored here and must not be.** `docs/journal/`
  is gitignored and `--commit`/`--push` refuse there. It *is* reachable as the
  private repo `jay79-boop/ray-vault` — attach read-only, and **never push**:
  their nightly backup does add/commit/push with no pull, so a commit from a
  session breaks that night's backup. `docs/vault-route.md`,
  `.claude/skills/vault-route/`. `tests/test_vault_boundary.py` fails CI if vault
  content lands here — cite the vault by repo name, never by note path.
- **Their local checkout**: `C:\Users\Gexio\OneDrive\pwb-toolbox` is canonical; a
  second one exists at `C:\Users\Gexio\pwb-toolbox`. `jay` and `upstream` mean
  the same in both; **`origin` does not** — use `jay`/`upstream` explicitly,
  never a bare `origin`. Never hand over a `merge` or `checkout` without the
  `fetch` on the same line, and end with a `Test-Path` so success is visible.
  For `main` use `git merge --no-edit jay/main`, never `--ff-only`. Do not read
  staleness out of an ahead/behind count. `docs/local-checkout.md`.
- **Never commit keys.** `.env` is gitignored; `.env.example` lists every
  variable. `.mcp.json` reads `API_KEY_21ST` from the *process* environment —
  `.env` alone does not reach it. The one committed key is the Amplitude browser
  ingestion key in `static/karaoke-queue.html`, public by design.

## The ledger

State lives in three places, split by how fast each kind of fact goes stale.
This file is the slow half.

- **`docs/state.md`** — operating state: fleet registry, roadmap, tech stack.
  Not auto-loaded; read it when you need it.
- **`docs/decisions/`** — one file per decision, newest first in its `README.md`.
  Append; never rewrite. A correction is a new entry that supersedes.
- **`.claude/skills/` + `docs/skills.md`** — the procedure for a job done often
  enough to be worth not retyping.
- **Nowhere at all** — open pull requests, their CI, what `main` points at, and
  any count of them. **Derive those from git and the GitHub tools at read time.**
  Written down, they were wrong within hours every time.

If you are about to write a PR number, a commit SHA, or "N open" into this file
or `docs/state.md`, that is the mistake the split exists to prevent.
