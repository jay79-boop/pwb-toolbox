# Skills: what each one is for, and when to retire it

A skill is a **unit of recurring work**. A pull request is a unit of change.
Those are different things, and conflating them is how a skills directory turns
into forty files that never fire.

The bar for adding one: **you have done this job at least three times, the
prompt is long, and you keep re-deriving the same steps.** Three of this repo's
skills were extracted after ~25, ~12 and ~10 pull requests had each re-run the
same procedure from memory.

## Why the count matters

Every skill's `name` and `description` load into **every session, every turn,
whether or not the skill fires**. Only the body is deferred. So the cost of a
skill is paid continuously and the benefit is paid occasionally.

That cost is real but second-order at this scale — the descriptions are worth
roughly a thousand tokens against a ~7,000-word `CLAUDE.md`. The first-order
cost is **trigger collision**: the more near-neighbour skills exist, the more
often the model loads the wrong one, and the more description text has to be
spent on disambiguation instead of triggering. It gets worse faster than the
token cost does.

The worked example is `build-puzzle-process`, which spent half its description
telling the model *not* to load it — and was retired on 2026-08-29 for exactly
that. When a description has to argue against its own trigger, the skill is
already telling you it does not belong.

## The three-way split

The same split the ledger uses, applied to skills:

| Where | Holds | Worked example |
|---|---|---|
| `CLAUDE.md` | The warning that must be in scope *without* a trigger | "a non-zero crash count is a converter bug" |
| `docs/<topic>.md` | The rationale, the evidence, the incident | `docs/backtesting.md`, `docs/converter-corpus.md` |
| `.claude/skills/<name>/` | The **procedure** — what to run, in what order, how to read what it prints | `backtest-trust`, `pine-converter` |
| `tools/`, `pwb_toolbox/` | The behaviour, with tests holding it honest | `verify_timezone`, `noise_floor` |

`backtest-trust` is the split working: `docs/backtesting.md` carries the two
incidents and the numbers, the skill carries how to run `verify_timezone` twice
and what a peak that never sharpens means, and `tests/test_backtest_lab.py`
holds the functions themselves honest. None of the three repeats another.

A skill that restates what a tool does will go stale silently, because nothing
in CI reads it. A skill that says *when to reach for the tool and in what
order* stays true for much longer. Point, do not repeat.

`tests/test_skills.py` enforces the mechanical half of this: every repo path a
skill names must exist, and every description must stay inside its budget. It
cannot check that the prose is still true — that part is on the author.

## Two homes, and they are not interchangeable

- **`.claude/skills/` in this repo** — versioned, reviewable in a pull request,
  visible to cloud *and* local sessions, and writable from either. Anything
  specific to this repository goes here.
- **Account-level (`gexio-machine`)** — synced from the owner's account, reaches
  every project, and **a cloud session cannot durably edit it**. Cross-project
  preferences go here, and they have to be written from a local session or
  pasted by the owner.

Putting a repo-specific skill in the account is how you get a skill that fires
in unrelated projects. Putting a cross-project preference in the repo is how you
get one that does not fire where you need it.

## Retirement

A skill that no longer fires is not free — it is still costing description
budget on every turn and still competing for triggers. Retire it.

**Review trigger:** whenever a new skill is added, or a tool a skill drives is
removed or substantially rewritten.

**Retire when any of these is true:**

1. **It has not fired in months and you cannot name the next time it would.**
   The honest test is whether you would write it today, not whether it was
   useful once.
2. **Its tool is gone or rewritten.** `tests/test_skills.py` will fail on a
   dead path — that failure is a retirement prompt, not a licence to fix the
   path and move on. Ask whether the skill still describes work you do.
3. **It has collapsed into another skill.** Two skills whose descriptions need
   to explain the difference between them should usually be one skill with a
   section each.
4. **The procedure moved into a tool.** The best outcome for a skill is that
   its steps become a tested command. When that happens the skill shrinks to a
   pointer, or goes.

**How:** delete the directory, note it in `docs/decisions/` if the reasoning is
worth keeping, and leave `CLAUDE.md` alone unless it pointed at the skill. Do
not archive skills into a graveyard folder — git already has one, and an unused
directory of near-miss descriptions is the collision problem in a costume.

## Vendored skills are exempt from the bar

`ui-ux-pro-max` tracks upstream. It is not held to the path or description
checks, is not reformatted by `black`, and is restored by `uipro init` — prune
the extra companions again after any upgrade.

**The exemption is a claim about provenance, and it is checkable.** A skill is
vendored if re-running its installer would bring it back; `git log` over the
directory says which commit put it there. `build-puzzle-process` was carried in
the `VENDORED` set for a week on the strength of the word alone — it arrived by
hand in the same commit that extracted `process-mapping` from it, six days after
`uipro init`, and `uipro init` would never have restored it. That mislabel is
not cosmetic: everything in `VENDORED` is exempt from the description budget, so
a wrong entry spends always-loaded context that the test then reports as free.

## `aiq-research`, and what `npx skills` leaves behind

Installed on 2026-09-02 by request, from the public NVIDIA catalogue:

```bash
npx skills add NVIDIA/skills --skill aiq-research --copy
```

It is a thin stdlib-HTTP client for a **NVIDIA AI-Q Blueprint server**, by
default at `http://localhost:8000`. Nothing in this repo runs one, so **expect
it never to fire.** That is the state it was committed in, knowingly — not a
bug to debug. If a backend never appears, this is the skill retirement rule
firing on schedule: it is dead weight, and it should go.

Three things about the install are deliberate:

- **`--copy`, not the default symlink.** Without it the installer leaves
  `.claude/skills/aiq-research` as a symlink into `.agents/`. Git stores that
  faithfully, and Git for Windows without `core.symlinks` checks it out as a
  *plain text file containing the target path* — so the skill would work in
  every cloud session and silently not exist in the owner's local checkout,
  which is the one that matters. `--copy` gives a real directory on both.
- **`.agents/` is gitignored.** `--copy` writes the same 88K twice; the
  `.claude/skills/` copy is the one every tool here reads. `skills-lock.json`
  is committed, so `npx skills experimental_install` restores the mirror on any
  machine that wants it.
- **It is *not* in the `VENDORED` set**, though it would qualify — `npx skills
  update` really would restore it. Exempting it would hide its description cost
  and stop `tests/test_skills.py` reading it at all. Left in, an upstream update
  that bloats the description or names a path we do not have turns CI red
  *before* the new version is trusted, which is the signal worth having. The
  cost of that choice is that a future `skills update` can redden CI on prose
  nobody here wrote. Read the failure as a review prompt for the new upstream
  version, not as something to patch locally — a local edit to the skill is
  reverted by the next update without a word.
