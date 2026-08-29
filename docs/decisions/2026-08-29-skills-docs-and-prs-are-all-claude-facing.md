# Skills, docs and pull requests are all Claude-facing; the owner had no door

*Decided 2026-08-29.*

**The question asked:** of the three places knowledge lands here — a skill, a
pull request, a markdown document — which is best for the owner?

**Decision:** none of them, and the framing is the finding. All three are
written for a Claude session to read. The gap is not between them; it is that
nothing in the system was built for the owner to read. So: add the missing
door (`tools/front_door.py` → `docs/desk-index.html`), give the prose the
check the skills already had (`tests/test_docs_paths.py`), and prune nothing,
because measurement said there was nothing to prune.

## What was measured

| | Volume | Who reads it | Checked by |
|---|---|---|---|
| Skills | 9 own + 2 vendored, ~4.5k chars of description loaded every turn | a session, when a trigger matches | `tests/test_skills.py` |
| Markdown | 51k words `docs/`, 9k words across 28 decisions, 3.5k `CLAUDE.md` | a session that goes looking | **nothing** |
| Pull requests | 151 merged, 148 of them inside 14 days, 0 open | nobody, after merge | CI |

Prose-to-code is 0.15 and prose drift — a document naming a `.py` that changed
after the document did — was 12 references of 153, 8%. **The three-way split
made on 2026-08-24 is working.** It is not the defect.

The defect is that a skill only exists inside a session, a pull request is the
record of what a session did, and 72,000 words of markdown is a 250-page book
aimed at someone `CLAUDE.md` itself describes as not retaining procedures
between sittings. The one surface built for the owner — the Action Ledger,
clickable, persistent, free to tick — carries action items and nothing else.

## The half that was deliberately not built

The owner asked for "where everything stands" on the new page: open pull
requests, CI, branch. **That was refused**, one day after
[Retiring the Live Work Dashboard](2026-08-29-retiring-the-live-work-dashboard.md)
turned down a static rebuild by name for carrying exactly those facts. Building
it would have reproduced a failure this repository had documented the previous
day, with fresh paint on it.

Instead the page opens with what it does *not* carry and links to the three
things that derive it: any session's own opening catch-up, the Action Ledger,
and GitHub's pull request list. `test_the_page_carries_no_live_state` is what
stops a later session adding a count anyway — it fails on a PR count, on a CI
claim, and on any commit SHA beyond the page's own build stamp.

## What the new check found on its first run

`tests/test_docs_paths.py` reads every backticked repo path in `CLAUDE.md`,
`docs/*.md` and `docs/decisions/*.md` and requires it to resolve. Six documents
failed. One was a live bug and five were the check being too crude:

- **`docs/blueprint-guide.md` sent the reader to two scripts that have never
  existed** — `tools/blueprint-xlsx-to-json.py` and its inverse, with the wrong
  flag, while the real `tools/blueprint_converter.py` does both as subcommands
  and validates as a third. The guide hedged with "(if you have it)", which is
  what a document does instead of failing. Fixed, and the corrected commands
  were run.
- Two were scanner bugs: brace expansion (`{greeks,decay}.py`) and pytest node
  ids (`file.py::test_name`) are not filenames.
- Three were correct prose about something outside this tree — a removed file
  in the past tense, a Claude Code convention, a machine-local file. They are
  listed with a reason each, and a test deletes an entry once its reason
  expires, so the allowlist cannot become the place broken links go to be
  forgotten. Guessing at these from nearby words was rejected: that is the
  proximity-regex mistake
  [already paid for](2026-08-24-a-written-rule-with-no-check-behind-it-lasted-eight-hours.md).

## Nothing was pruned, and that is the result

Pruning was asked for and measured four ways before being declined:

1. The eight documents with a single inbound reference are each referenced from
   `CLAUDE.md` or `docs/layout.md` and each document a tool that still exists.
   One reference from the index is correct structure, not orphanhood.
2. `docs/layout.md` covers the tree completely — every tool, package and page
   appears, three of them in prose rather than as bullets.
3. Prose-to-code at 0.15 is not bloat.
4. Sixteen near-duplicate passages exist across the layers. The cross-layer ones
   are the design working — `CLAUDE.md` carries the warning that must be in
   scope without a trigger, the document carries the evidence. The peer-layer
   ones are single shared sentences each document needs to stand alone.

Deleting something to look productive would have cost information and bought
nothing.

## The general lesson

**Ask who reads it before asking which form it takes.** Three years of ledger
design here went into stopping Claude sessions writing stale state, and it
worked. None of it made the project legible to the person who owns it, because
that was never the question being answered.
