# signals/

Two files that are **tracked on purpose**, which makes this directory the
exception to every other data directory in the repository.

`spec_desk/`, `night_lab/`, `season/`, `engagements/` and `awareness/` are all
gitignored, because this fork is public and each of them holds the owner's own
records. These two are not those. They are the *shape* of a domain with the
content taken out:

```
desk.json      counts, ages, streaks and a three-state broker flag
content.json   render segment counts, post counts, plan state -- in two halves
```

## Why they are in git at all

Because git is the only thing a cloud session and the owner's Windows machine
share. `tools/desk_agent/runs.jsonl` is committed for exactly this reason and
pushed by the launcher after every run; these follow it. Without them,
`tools/awareness.py` lists the desk and content under *not visible from here*
and stays there forever, which is an honest report of a permanent blind spot
rather than a solution to one.

## Why it is safe to publish them

Not because anyone remembered to leave the ticker out. Every field in both files
is declared in a schema, and the only values that schema permits are integers,
floats, booleans, ISO dates, and words from a closed vocabulary. `validate()`
raises `Unpublishable` before a byte is written, so **there is no free-text field
for a symbol, a price, a balance, a caption or a filename to reach**.
`tests/test_desk_signal.py` and `tests/test_content_signal.py` plant all of those
in the inputs and assert none survive.

## Writing them

```bash
python tools/desk_signal.py emit            # on the owner's machine
python tools/content_signal.py capture --platform-json -   # from a session with the connectors
```

Neither file is generated in CI and neither is required to exist. When one is
missing the awareness layer says so by name — a missing signal is reported as a
blind spot, never as a quiet domain.

## When one conflicts

Re-emit rather than merge. Each file is a whole-file replacement written by one
machine, so a hand-merged signal would describe a moment that never happened.
