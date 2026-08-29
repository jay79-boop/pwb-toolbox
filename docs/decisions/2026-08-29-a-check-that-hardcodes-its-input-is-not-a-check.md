# A check that hardcodes its input is not a check

*Decided 2026-08-29.*

The Action Ledger is published with a small Node harness that stubs a DOM, runs
the page's own JavaScript, and asserts the things that have actually broken
before: every item renders, a tick publishes, the emitted document is
doctype-first with its scripts closed, and the CSS and JS round-trip
byte-identical. It exists because a publish earlier in the week shipped a blank
page — the JS had been rebuilt from a truncated copy and stopped mid-string, and
nothing caught it until the owner opened the page and saw nothing.

Today that harness was invoked as `node harness.mjs ledger.html` and printed:

```
1. renders 41 items, 11 open
...
ALL CHECKS PASSED
```

The page passed to it had 63 items and 9 open. Line 4 of the harness read
`fs.readFileSync("ledger-body.html", "utf8")` — a stale copy from five hours
earlier — and the argument was discarded. It had validated a file nobody was
about to publish, and said so in words that mean the opposite.

## Why this is worse than having no harness

A missing check leaves you knowing you have not checked. A check that reads the
wrong subject converts *I did not verify this* into *I verified it and it was
fine*, and that second state is the one you act on. The publish that followed
would have gone out on the strength of a green line about a different document.

The counts were printed. `renders 41 items` against a 63-item page was on screen,
one line above `ALL CHECKS PASSED`, and the summary is what gets read. Output
that names what was checked is only useful if it is the part that fails.

## The rule

**A check takes its subject as an argument, and its output names the subject it
read.** Not a default, not a constant near the top of the file, not a path that
was right when the file was written.

The repository's test suite already works this way and did not have this problem:
`tests/test_docs_paths.py` walks the real `docs/` tree at run time,
`tests/test_skills.py` reads the skills actually on disk, and
`tests/test_desk_agent_gameplan_path.py` parses the live `premarket.md` rather
than restating the path it expects to find there. None of them can be pointed at
the wrong thing, because none of them names a thing.

They go one step further, and that step is the one the harness was missing: both
`test_docs_paths.py` and `test_skills.py` pin a floor on how much their glob
found, because — in the words of the second — "a silent zero would make every
test below pass". Reading the wrong file and reading no file are the same defect
wearing different clothes, and the cure had already been invented here.

## What the existing discipline did and did not cover

Convict-and-acquit — pin a check by confirming it fails against the broken state
— is the standing rule here, and it *would* have caught this: break the file you
pass, watch the harness pass anyway. It was never applied, because the harness
was filed as a tool rather than as a test, and tools were tacitly exempt.

They are not. Anything whose output is used as evidence is a test, wherever it
lives and whatever it is called. The exemption was the mistake, not the harness.

## What is and is not committed

The harness is session scratch and stays there — it verifies an artifact, not
this repository, and committing it would put a second copy of the ledger's
rendering logic under `tools/` to go stale. The rule above is the part worth
keeping, which is why it is here rather than in a file next to the code.
