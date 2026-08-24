# pwb-toolbox

A toolbox library for quant traders: datasets, backtesting (Backtrader), live
execution, and performance analytics. Requires Python 3.10+.

This file is loaded into every session, so it is kept short on purpose. It holds
the rules and the traps; the detail lives in `docs/` and is linked from here.

## Where the work happens

The owner works in **Claude Code running locally on their Windows machine**, and
talks to it in plain English rather than pasting shell commands. Away from the
desk they reach that same local session from their phone over Remote Control; a
scheduled task, `\ClaudeRemoteControl`, keeps one alive from logon.

Cloud sessions also read this file, and the capability gap between the two is
load-bearing — getting it wrong cost two days once. A local session can touch
`C:\Users\Gexio\...`, run their venv, and open the trade journal. A cloud session
shares only GitHub: no disk, no scheduled tasks, no skills directory, so
everything there is done by handing over a command and waiting for output. Both
see this identical repository, which is why they are easy to confuse.

**Run the `gexio-machine` skill before writing a single command for them.** It
carries the local-vs-cloud diagnostic, the PowerShell traps, and the machine
facts. Details in `docs/local-checkout.md`.

They do not retain procedures between sittings, and should not have to. The
orientation hook opens every session with a short unprompted catch-up. **"Catch
me up"** asks for that again mid-session: where things stand *now* — branch,
working tree, what is in flight — not a replay of how it got there.

## Brainstorm before building

The owner asked for this after several rounds where a guess was shipped and they
found the hole in it — a spreadsheet reporting a portfolio value four tenths of
which had not moved since 2022, a ladder that collapsed to `#N/A` on a
hand-typed ticker.

For anything beyond a small correction: explore what they are actually trying to
do, put the trade-offs to them, and get an answer before writing code. Ask few
questions and make them count.

**Put it in a box they can click.** Use `AskUserQuestion` so they tick rather
than type, lead each option list with a recommendation, and say which one you
would pick and why. Use `multiSelect: true` wherever more than one answer can be
true at once — their answers are often combinations ("do 1 but incorporate 2 and
3"). Prose is only for what a box cannot carry: a PowerShell step they must run,
and an answer only they hold, like a number or a URL.

**Push back.** They want the disagreement, not the compliance. If their framing
has a flaw, say so before building to it. If something they asked for last week
is now dead weight, raise it rather than maintaining it silently. Recommend the
thing you would do if it were yours.

This is a standing preference and it is not limited to this repository. The
cross-project copy belongs in the `gexio-machine` skill, which is synced from
their account — a cloud session cannot durably edit it, so that copy has to be
written from a local session or pasted by them.

## Flagging action items

Anything the user has to do themselves — export a key, restart something, click
through an OAuth flow, make a decision — goes in one `## 🔴 NEEDS YOU` block at
the very end of the reply, never buried in a paragraph.

How to write that block lives in the `gexio-machine` skill: heading and numbering
rules, the one-self-contained-paste rule, naming the program each step goes into,
what success looks like, GUI steps, prompting with `Read-Host` rather than
hand-edited commands, and the PowerShell traps. The skill loads before any
command written for the user to run, and its copy is the one that reaches
sessions outside this repository — so it is deliberately not restated here.

One rule that is *not* in the skill: if a step is also explained in prose above,
it still gets repeated in the block. The block is the checklist of record.

## Layout

- `pwb_toolbox/` — the shipped package (`datasets`, `backtesting`, `execution`,
  `performance`, `scraping`, `converting`, `options`, `journal`)
- `pwb_toolbox_legacy/` — superseded code kept for reference; not public API
- `tests/` — pytest suite
- `tools/` — the desk: trade cards, ladders, labs, scanners, the unattended desk
  agent, and the operational IB scripts
- `static/` — single-file browser tools that open from `file://` with no build
  step, plus the shared JS modules they load
- `pine/` — TradingView strategies kept as reviewable source; nothing under
  `pwb_toolbox/` imports them
- `docs/` — manuals, field notes, and the decision log
- `.claude/skills/` — the procedures worth not retyping. `docs/skills.md` is the
  bar for adding one and the rule for retiring one; read it before writing a skill

**`docs/layout.md` is the full inventory** — every tool, what it does, and the
reasoning behind it. Read it when you need to know whether something already
exists before building it.

## Environment

Dependencies live in `requirements.txt`; `requirements-dev.txt` adds `pytest`.
CI (`.github/workflows/tests.yml`) runs two jobs on Python 3.11: `test`
(`pytest tests/ -v`) and `format` (`black --check --diff pwb_toolbox/ tools/
tests/`). They are separate jobs so a formatting nit cannot mask a real test
failure, or the reverse.

In Claude Code on the web, `.claude/hooks/session-start.sh` builds a `.venv/`,
installs `requirements-dev.txt`, and exports `PATH`/`PYTHONPATH` so bare
`python`, `pytest` and `black` resolve to it. It runs **asynchronously** — if an
import of a third-party package fails in the first moments of a session, the
install is most likely still running; re-run rather than treating it as a real
failure. The hook is a no-op locally.

By hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH="$PWD"
```

Tests import `pwb_toolbox` from the repo root, not from an installed
distribution. `pythonpath = ["."]` in `pyproject.toml` covers `pytest` (including
in CI); the exported `PYTHONPATH` covers ad-hoc `python -c` invocations.

`.github/workflows/delete-merged-branch.yml` deletes a PR's head branch on merge,
because **ref deletion is the one git operation a Claude Code container cannot
perform** — the session's git proxy returns 403. It needs Settings → Actions →
General → Workflow permissions set to "Read and write". A branch still in use
simply comes back on the next push. Note the trap it cannot guard:
`docs/decisions/2026-08-24-deleting-a-merged-head-branch-orphans-any-pr-stacked-on-it.md`.

## Commands

```bash
pytest tests/ -v                  # full suite (~28s cold / ~15s warm)
black pwb_toolbox/ tools/ tests/  # format; CI checks this exact scope
black --check --diff pwb_toolbox/ tools/ tests/   # what CI runs
node static/option-lab.test.js    # greeks/ladder math (also run by pytest)
node static/journal-shots.test.js # screenshot sizing/budget (also run by pytest)
node static/process-grammar.test.js  # branch grammar (also run by pytest)
pytest tests/test_skills.py -q    # skills: live paths, description budget

python tools/trade_card.py plan --help    # pre-trade card + hold-time checker
python tools/analyze_trades.py export.csv # diagnose a Schwab transaction export
python tools/spend_watch.py audit snapshot.json  # what is draining the window
python tools/night_lab.py plan            # queue tonight's stress jobs
python tools/season_scan.py report        # seasonality: report + watchlist + json
python tools/calibration_audit.py --symbols SPY  # is our option math calibrated?
python tools/engagement.py list           # readiness engagements and where each stands
python tools/ai_company.py gates          # can any agent commit money unsupervised?
python -m tools.desk_agent.runlog summary --last 20  # is the agent actually working
```

`docs/layout.md` lists the rest with what each is for.

## Conventions

- Formatting is `black` with default settings. `black pwb_toolbox/ tools/ tests/`
  is the formatted surface and is gated by the `format` CI job. Do **not** run
  bare `black .`: it would rewrite `pwb_toolbox_legacy/` (kept as-is) and the
  vendored skill under `.claude/skills/` (tracks upstream, restored by
  `uipro init`) — neither is in the gated scope. `black` is pinned in
  `requirements-dev.txt` because its stable style changes each January; bump the
  pin deliberately, reformatting in the same commit.
- Do **not** pin an exact test count in this file. Every branch adding a test
  would touch that one line, so any two open PRs collide on it. `pytest` prints
  the real total.
- Tests must not require network access or a live broker. `ib_insync` is
  exercised against a mocked `IB` client; dataset tests must not depend on
  `PWB_API_KEY` or a Hugging Face login. `pwb_toolbox.scraping` follows the same
  rule — HTTP via a fake session, and `PoliteSession` takes injectable
  `sleep`/`monotonic` so rate-limiting is asserted without real delays.
- `pwb_toolbox.converting` emits Backtrader source, so its tests compile the
  generated code and run it through a real `cerebro` on synthetic bars. A
  conversion that parses but does not execute or trade is a failure, not a pass.
- Regression tests for fixed bugs pin the previous numeric output where the old
  behavior must be preserved (see `_LEGACY_DEFAULT_QUOTE` in
  `tests/test_optimal_limit_order.py`).

## Do not use the user as a test harness

Every round trip that asks them to paste a command, report output, and wait costs
them far more than it costs us, and most of what it found was reachable from
here. For the converter specifically, build a corpus and sweep it rather than
asking — `docs/converter-corpus.md` has the commands and the proxy limits
(`raw.githubusercontent.com` and `git clone` work; `api.github.com` and
`codeload.github.com` return 403).

A non-zero crash count from `tools/pine_sweep.py` is a bug in the converter, not
a fact about the corpus. `convert` is contracted never to raise.

## The desk agent runs itself, and reports on itself

`tools/desk_agent/` is a Claude Code agent that runs on a schedule and revises its
own playbook. Two things are deliberate and both look like oversights:

- **The run log is committed.** `runs.jsonl` is tracked because it is the only
  part of the agent a cloud session can see, and `git log` over it is the audit
  trail. Raw stdout under `logs/` is ignored — noise, and it can carry chart
  detail.
- **The agent may not edit its own guardrails, its own log, or `runlog.py`.** The
  weekly review rewrites the rest of the playbook freely and opens a draft PR;
  that is the self-improvement loop. But an agent that can loosen its own limits
  does not have limits, and one that can edit its own record cannot be reviewed.
  A review that wants to change those three has to stop and say so.

Autonomy ceiling is "everything except order entry", on a TradingView login with
no broker connected. Reasoning in `docs/tradingview-agent-security.md`; setup in
`tools/desk_agent/README.md`.

## Backtesting: two things that produced confidently wrong answers

`tools/backtest_lab.py` exists because a single-instrument backtest here produced
a result that survived every check applied to it and was still wrong twice over.
Both failures are cheap to repeat, neither announces itself, and both are pinned
by tests in `tests/test_backtest_lab.py`.

- **A feed's timestamps are in whatever zone the vendor chose, and the file does
  not say.** histdata's ASCII M1 exports are New York local time *with DST*, not
  the fixed EST usually assumed. An ICT session strategy measured +39 points over
  eight years read wrongly, +7 read correctly. Verify with `verify_timezone`
  against a known-zone feed, comparing **returns** in a winter month and a summer
  one — an offset that changes between them is DST.
- **A result means nothing until it clears the disagreement between two vendors
  of the same instrument.** One strategy on the S&P from two feeds gave +50bp and
  −234bp over the same eight years while correlating 0.93 year over year. Run
  `noise_floor()` before believing any number. A high correlation with a large
  mean gap is the dangerous case, and the one actually seen.

Two habits follow: compare in **basis points of price**, never raw points; and
**charge costs always** — the strategy above is gross-positive and net-negative.
Full account in `docs/backtesting.md`.

## The trade journal is not in this repository

`trade-journal.html` lives only on the user's machine at
`C:\Users\Gexio\OneDrive\trade-journal\`. It was briefly committed here and
removed at their request: it is a personal document and this fork is public.

- **Ask for the file, do not reconstruct it.** Git history can recover the
  revision that was removed, but their copy has moved on — treat history as
  reference and the file they send as truth.
- **It inlines `static/option-lab.js` and `static/journal-shots.js` verbatim**,
  each behind a comment saying so, because the journal must stay one file that
  opens from `file://`. Edit the module here, run the tests, then re-inline the
  whole file — never patch the inlined copy, or the tested version and the
  running version stop being the same code.

## The user's local checkout

Windows, PowerShell 5.1, at `C:\Users\Gexio\OneDrive\pwb-toolbox`, Python 3.12.
**A second checkout exists at `C:\Users\Gexio\pwb-toolbox` without the OneDrive,
and the OneDrive one is canonical.** Sessions have landed work in the wrong one,
so always spell the OneDrive path out rather than assuming the working directory.

- **`jay` and `upstream` mean the same thing in both checkouts; `origin` does
  not** — it is upstream in the OneDrive checkout and the fork in the other. Use
  `jay` and `upstream` explicitly and never write a bare `origin` command.
- **Never hand over a `merge` or `checkout` without the `fetch` on the same
  line.** Both fail by succeeding. Write
  `git fetch jay <branch>; git merge --ff-only jay/<branch>` as one line, and end
  it with a `Test-Path` on a file the new commit adds so success is visible.
- **Do not read staleness out of an ahead/behind count.** It compares two refs,
  not two working trees, and means nothing when the refs are a feature branch and
  someone else's `main`. Check `git log` and the actual files.

`docs/local-checkout.md` has the rest: the second checkout's near-empty `.venv`,
the `PermissionError` on `pytest-current` that is Windows symlink cleanup rather
than a test failure, and the six-package subset that runs `scraping` and
`converting` without pulling in all of `requirements-dev.txt`.

## Design tooling, and credentials

`.claude/skills/` vendors the MIT-licensed ui-ux-pro-max suite — a local CSV
database of styles, palettes, font pairings and UX guidelines, queried with
stdlib Python, no network and no API key. It is unrelated to the trading library;
nothing under `pwb_toolbox/` imports it and `pytest` never touches it.

```bash
python3 .claude/skills/ui-ux-pro-max/scripts/search.py "fintech dashboard" --domain color --json
```

Credentials: `load_dataset` reads `PWB_API_KEY`, falling back to the Hugging Face
Hub and then yfinance. `.mcp.json` reads `API_KEY_21ST` from the *process*
environment — `.env` alone does not reach it. **Never commit keys**; `.env` is
gitignored and `.env.example` lists both variables.

`docs/design-tooling.md` covers the rest: how `docs/index.html` was built and why
it is self-contained, the GitHub Pages branch-source setup and why
`docs/.nojekyll` is load-bearing, why the Pages workflow was deleted rather than
fixed, the companion skills to prune after `uipro init`, and the two things that
bite when supplying `API_KEY_21ST` to a cloud session (allowed domains, and the
absence of a secrets store).

---

# The ledger

Project state lives in three places, split by how fast each kind of fact goes
stale. This file is the slow half: what the project is, how to work in it, and
the traps that cost real days.

- **`docs/state.md`** — operating state: fleet registry, roadmap, tech stack,
  honesty lessons. Hand-maintained, changes on the order of weeks. Not
  auto-loaded; read it when you need it.
- **`docs/decisions/`** — one file per decision, newest first in
  `docs/decisions/README.md`. Append a new file; never rewrite an old one. A
  correction is a new entry that supersedes.
- **`.claude/skills/` + `docs/skills.md`** — the *procedure* for a job done often
  enough to be worth not retyping; the doc carries the bar for adding one, the
  two homes a skill can live in, and the retirement rule. Rationale stays in
  `docs/`, behaviour stays in `tools/` — a skill that restates either goes stale
  silently, because only `tests/test_skills.py` reads a skill at all.
- **Nowhere at all** — open pull requests, their CI, what `main` points at, and
  any count of them. **Derive those from git and the GitHub tools at read time.**
  They were written down for months and were wrong within hours every time.

If you are about to write a PR number, a commit SHA, or "N open" into this file
or into `docs/state.md`, that is the mistake the split exists to prevent.

**Why it was split.** Every branch doing real work also edited one dense region
of prose here, so branches conflicted on `CLAUDE.md` and on nothing else — and on
2026-08-24 one branch merged with *zero* conflicts while silently reverting the
whole region to a state three merges stale. A conflict at least stops you and
demands a decision; a clean merge of two contradictory claims just picks one.
Per-file decisions removed the shared insertion point, and deriving the volatile
facts removed the thing worth fighting over.
