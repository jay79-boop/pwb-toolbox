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
written from a local session or pasted by them. What a cloud session *can* now do is
check whether the copy it was served matches the one the machine runs — the vault
mirrors the machine copy nightly. Doing that found the two disagreeing, which is
the worked example in `docs/vault-route.md`.

## Do the work. Hand back only what genuinely needs them

Asked for on 2026-08-28, after a reply handed over four steps of which one — "run
`git log --oneline jay/main..main` and paste it back" — was answerable from the
session in two commands. It was: their `main` tracks `origin`, which is *upstream*
in the OneDrive checkout, and upstream carried a commit the fork did not. Fetching
upstream and diffing the two settled it with no round trip at all.

**Before any item goes in a NEEDS YOU block, it has to survive one question: is
there any route by which this session could do it?** If yes, do it.

Only these are genuinely theirs:

- a credential, a code, or an answer only they hold
- a GUI action, or a click in a service this session cannot reach
- a command that must run on their Windows machine when the session is in the
  cloud and has no path to that disk
- a decision that changes money, scope, or something hard to reverse

Everything else is ours: reading files, running the suite, checking CI, git
archaeology, working out which commit diverged and why, comparing two remotes,
fetching a public repo to answer a question about it, writing the code and
opening the PR. **"Run this and tell me what it says" is almost always a failure
to try it here first.**

And batch what is left. One paste that does three things beats three numbered
steps; a diagnostic step that only feeds the next step should just be folded into
it.

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

Rules that are *not* in the skill (the count in this line went stale three
times, so it no longer carries one — just add a bullet):

- **Never indent prose underneath a checkbox.** Four-space indentation renders
  as a *code block*, and a code block reads as "paste this". On 2026-08-29 they
  pasted a sentence of explanation — "Docs, one skill, one test..." — into
  PowerShell and got `Missing argument in parameter list`, twice. Nothing ran and
  nothing broke, but the step stalled and the reply had to be sent again. Put the
  explanation on the checkbox's own line, or in prose above the block. **An
  indented block inside a NEEDS YOU item means "this is pasteable" and nothing
  else.**
- **Point at the exact place, never at where to look for it.** Asked for
  2026-08-29, after a step read "delete the branch on GitHub" and they replied
  "how I find the branch again? not familiar to working in github. always point
  to exacted location". So: give the URL that lands on the thing itself, filtered
  to it where the site allows — `.../branches/all?query=<name>` rather than
  `.../branches` — then name the control to click and what happens after. Naming
  a site, a page, or a menu path is a description of a search, and they have to
  run it. This holds for anything with an address: a settings page, a specific
  file in a repository, a run in Actions, a row in the ledger.
- **If a step is also explained in prose above, it still gets repeated in the
  block.** The block is the checklist of record.
- **Every item is a markdown checkbox — `- [ ]` — never a bullet or a number.**
  Asked for 2026-08-24: they work through the block across sittings and need to
  see which steps are already done, because an unticked list of five items and a
  half-finished one look identical. Sub-steps of a single item stay plain text
  underneath it; only the things they must actually *do* get a box.
- **The block is also copied into the Action Ledger**, and that is the copy that
  survives:

      https://claude.ai/code/artifact/a9da0f16-1b7f-4658-a21f-70271be5c413

  A checkbox in a terminal reply is text — there is nothing to click, and it
  scrolls away with the session. The ledger is a published artifact holding the
  `artifact` capability, so ticking a box republishes the page: the state *is*
  the page, it follows them to another device, and **a tick costs no tokens
  because it never involves a session at all.**

  **Append to it; never start a second one.** Read it with the Artifact tool
  (`action: "read"`, that URL), add your items to the state JSON in
  `<script id="app-state">`, and republish with `url` set to that same address.
  Items already ticked stay ticked. An item raised weeks ago staying visibly
  open is the entire point — a fresh list every session is what they asked us to
  stop doing. Mark anything *you* completed as `"who": "claude"`, `"done": true`
  so they can tell at a glance which items are still theirs.

  **Tick every row you can confirm is done — including theirs.** Corrected
  2026-08-29. The rule used to be that a session ticked only its own `who: claude`
  rows, on the reasoning that *a tick from them means they did it* and the `who`
  field carries that signal. It does, but the field already records it: `who` says
  who was responsible, and it keeps saying so after the box is checked. Ticking
  never overwrote that signal, so the rule was protecting nothing and charging
  them a trip to the ledger for work already proven finished.

  They asked for the change in plainer terms: *stop asking me to waste time
  looking and ticking it off.* It is the same rule as "do the work, hand back only
  what genuinely needs them", applied to the ledger itself — a confirmed row is
  not a decision, a credential, or a GUI action, so it was never theirs.

  So: when you establish a row is done, tick it in the same turn, whoever did the
  work, and say in the row what confirmed it. Leave it open only when you cannot
  confirm it.

  The bar is unchanged and is the whole safeguard: *verified*, not *believed* —
  tick when the work is confirmed by something outside your own reasoning: a test
  run, an API read, a file checked, an artefact off their disk. Otherwise leave it
  open and say what is still unproven. Never tick an item to tidy the list, and
  never tick one because the user said they did it without something checkable
  behind it. Re-reading a large ledger to tick one row is the cost of keeping the
  record true, and it is the right trade.

## Layout

- `pwb_toolbox/` — the shipped package (`datasets`, `backtesting`, `execution`,
  `performance`, `scraping`, `converting`, `options`, `journal`)
- `pwb_toolbox_legacy/` — superseded code kept for reference; not public API
- `tests/` — pytest suite
- `tools/` — the desk: trade cards, ladders, labs, scanners, the unattended desk
  agent, and the operational IB scripts
- `static/` — single-file browser tools that open from `file://` with no build
  step, plus the shared JS modules they load

**Pages and artifacts are light, colour-coded, and validated, by default** —
`docs/page-style.md` is the standing style and does not need to be asked for.
Committed light (no dark theme), every surface and ink stated so the page holds
on a dark host, colours run through the `dataviz` validator rather than
eyeballed, and every number on a page derived from the data rather than typed
into it.
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

`.claude/hooks/session-size.sh` runs on every prompt and warns once when this
session's own accumulated context passes 10M, 25M and 50M cache reads. It reads
the transcript the harness already writes, so it costs nothing — a spend warning
that spends the window is not one worth having. Silent below the first tier.

**That hook is scoped to this repository.** `tools/install_spend_hook.py` installs
a self-contained copy into `~/.claude/` so it fires in *every* session, whatever
project — and adds the Action Ledger rule to the user-level `CLAUDE.md` at the same
time. It has to run **on the machine the sessions run on**: a cloud container's
`~/.claude` is reclaimed with the container, so a cloud session cannot install it.
Both hooks share a tier state file, so a pwb-toolbox session warns once, not twice.

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
node static/strategy-lab-stats.test.js  # dashboard math (also run by pytest)
node static/karaoke-qr.test.js    # the QR the screen draws (also run by pytest)
pytest tests/test_skills.py -q    # skills: live paths, description budget

python tools/trade_card.py plan --help    # pre-trade card + hold-time checker
python tools/analyze_trades.py export.csv # diagnose a Schwab transaction export
python tools/spend_watch.py audit snapshot.json  # what is draining the window
python tools/spend_watch.py session <transcript>.jsonl  # is this session too big
python tools/install_spend_hook.py --check  # size warning in EVERY session? (local only)
python tools/install_workspace_dirs.py --diagnose  # why can't this chat see my other repo?
python tools/night_lab.py plan            # queue tonight's stress jobs
python tools/season_scan.py report        # seasonality: report + watchlist + json
python tools/calibration_audit.py --symbols SPY  # is our option math calibrated?
python -m tools.strategy_lab               # live run dashboard on :8771
python tools/reversal_15m_sim.py bars.csv --post  # send that run to it
python tools/fetch_bars.py BTC/USDT --exchange coinbase --days 365 --out a.csv  # bars with real volume
python tools/engagement.py list           # readiness engagements and where each stands
python tools/ai_company.py gates          # can any agent commit money unsupervised?
python -m tools.desk_agent.runlog summary --last 20  # is the agent actually working
python tools/desk_levels.py levels NQ=F --markdown  # session levels/FVGs, no chart needed
python tools/nvidia_vision.py ask chart.png --prompt "what is this"  # read an image with a vision model
python tools/desk_watch.py check          # which sessions did the desk not report?
python tools/obsidian_sync.py vaults      # which Obsidian vaults exist here (local machine only)
python tools/obsidian_sync.py sync --dry-run  # local mirror only; docs/journal is gitignored by decision
python tools/front_door.py build      # rebuild the desk index: what we have, and every decision
python -m tools.karaoke_server.sim report  # does the random singer queue stay fair?
python -m tools.karaoke_server.queue_server  # run a karaoke night: screen + phone QR joins (LAN only)
python tools/karaoke_server/build_standalone.py  # one-file karaoke_os.py for any other computer
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

**Whether a job needs a desktop is a per-job fact, and it decides how its task
is registered.** A Windows task set to run whether the user is logged on or not
gets a logon session with **no desktop** — fatal to a job driving TradingView
Desktop, irrelevant to one that does not. `premarket` and `journal` read their
levels from bar data via `tools/desk_levels.py` and render their own images
headless, so their tasks carry a stored credential and run signed in or not;
`alerts` and `pine_loop` still drive the chart and still need a desktop. The
fact lives in `register_desk_agent.ps1`'s `$jobs` table as `NeedsDesktop`, is
mirrored in `run_job.ps1` and `autologon.ps1`, and a test asserts all three
agree. **`S4U` is banned for every job regardless** — it carries no credentials,
so DPAPI secrets and OneDrive paths both fail at run time, unattended. Two
decision records:
`docs/decisions/2026-08-29-the-logon-type-is-not-the-bug.md` and
`docs/decisions/2026-08-29-the-jobs-stopped-needing-a-desktop-so-the-tasks-stopped-needing-one.md`.

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

## Nor is the Obsidian vault — do not mirror it here

`tools/obsidian_sync.py` can mirror the vault into `docs/journal`. **Decided
2026-08-29: it is not pointed at this repository.** `docs/journal/` is gitignored
and `--commit`/`--push` refuse there, so a sync into this fork now fails by
design rather than by anyone remembering.

The reason is not that the vault is dangerous — the content checked clean. It is
that a session here needs the vault's *rules*, and `docs/vault-operating-manual.md`
already carries those, canonically. Mirroring the rest into a public fork bought
nothing. Two sessions built opposite halves of this on the same day without
knowing, so: **do not re-point it here, and do not work around the guard.** Use
it for a local mirror if you want one.

Reasoning in
`docs/decisions/2026-08-29-a-tool-that-needs-a-local-path-should-find-it.md`.

**But it is reachable, and that is a different thing.** The vault is the private
repo `jay79-boop/ray-vault`; a cloud session attaches and clones it in about a
minute. So "a session here cannot reach the vault" — the premise under the
paragraph above — is **has not**, not cannot. Read it whenever you need the vault
rather than its rules, and **never push to it**: the owner's nightly backup does
`add`/`commit`/`push` with no `pull`, so a commit from a session breaks that
night's push and lands the failure on them. `docs/vault-route.md` has the route
and the reasoning, `.claude/skills/vault-route/` the procedure, and
`tests/test_vault_boundary.py` fails CI if vault content lands in this public fork
— cite the vault by repo name, never by note path.

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
