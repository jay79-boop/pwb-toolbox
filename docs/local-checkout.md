# The user's local checkout


Windows, PowerShell 5.1, at `C:\Users\Gexio\OneDrive\pwb-toolbox`, Python 3.12.

**A second checkout exists at `C:\Users\Gexio\pwb-toolbox`, without the
`OneDrive`, and the OneDrive one is canonical.** It holds the live feature
branches and sits in a folder with version history behind it. Sessions have
landed work in the other one by mistake, so always spell the OneDrive path out
in a command rather than assuming the shell's working directory.

**Do not read staleness out of an ahead/behind count.** An earlier version of
this note recorded the OneDrive checkout as "sixteen files and 1,834 lines
behind", and that was an artifact, not a fact — it was sitting on a feature
branch whose `main` had simply never been fast-forwarded, and every file the
note called missing was in fact present. Ahead/behind compares two refs, not two
working trees, and it means nothing about currency when the refs are a feature
branch and someone else's `main`. It is the same trap as the `[ahead 113,
behind 1]` reading below. Check `git log` and the actual files before believing
either.

If you end up in that second checkout anyway, know that **its `.venv` is nearly
empty**: the suite reads as broken when it is merely uninstalled, which sends you
hunting a bug that does not exist. Install first, then run tests through the venv's
own interpreter rather than a bare `pytest`:

```
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest tests/ -q
```

If teardown throws `PermissionError` on `pytest-current`, add
`--basetemp="$TEMP/pwbtest"`. That is Windows symlink cleanup, not a test failure —
worth saying because the traceback looks exactly like one.

Separately, `.claude/settings.local.json` regrows dead permission entries on its
own, so cleaning it is never a one-time fix. That is Claude Code behaviour rather
than a fact about this repository, so it lives in the `gexio-machine` skill along
with the fix — not restated here, for the same reason the NEEDS YOU rules are not.

**`jay` and `upstream` now mean the same thing in both checkouts. `origin` does
not.** As of 2026-08-18, both directories have `jay` = the fork
(`jay79-boop/pwb-toolbox`) and `upstream` = the upstream project
(`paperswithbacktest/pwb-toolbox`). `origin` is the one that still differs: it is
**upstream** in the OneDrive checkout and **the fork** in the other, so a bare
`git pull origin main` means two different things depending on which directory
the shell happens to be in, and fails by succeeding against the wrong project
rather than by erroring.

**Never hand over a `merge` or `checkout` without the `fetch` on the same line.**
Both fail by succeeding. `git checkout <branch>` on a branch that already exists
locally is a no-op that reports "Already on ..." and brings nothing down;
`git merge --ff-only jay/<branch>` merges the remote-tracking ref *as of the last
fetch*, so it happily fast-forwards to a commit that is already stale. Each cost a
round trip on 2026-08-22, and in both cases the terminal said what had happened —
"Your branch is behind ... by 2 commits" — while the next step failed with an
unrelated-looking error about a missing file. Write
`git fetch jay <branch>; git merge --ff-only jay/<branch>` as one line, every time,
and end it with a `Test-Path` on a file the new commit adds so success is visible.

**Never pin a handed-over command to a feature branch.**
`.github/workflows/delete-merged-branch.yml` deletes a PR's head branch the
moment it merges, so a line built around `git fetch jay claude/<slug>` stops
working at exactly the point the owner gets round to running it. It fails with
`couldn't find remote ref`, which reads like a network fault rather than like
success. Once the PR is merged the commit is on `main` — fetch that instead. If
the branch has to be named because the PR is still open, say in the same line
that it expires on merge. Seen on 2026-08-29; the workflow doing the deleting is
ours and is working as designed, which is why this is a habit rather than a bug
to fix.

**Do not chain anything after `git log`, `git diff` or `git show`.** Git pipes
long output through a pager, which parks at `(END)` and holds the line open
waiting for a keypress. The output above it looks complete, so the natural move
is to copy it and report back — and everything after the `;` runs only if you
press `q` first. On 2026-08-29 a handed-over line listed 31 commits and then
merged them; the reply came back ending in `(END)`, and the merge had not run.
Nothing in that output says so. Write `git --no-pager log ...` in anything handed
over, or put the log on a line of its own with nothing after it.

**So use `jay` and `upstream` explicitly and never write a bare `origin`
command.** `git fetch jay <branch>` now works identically in both — which was not
true before: the second checkout had no `jay` remote at all, so the command this
file documented silently failed there. It also had no route to upstream, which is
why the OneDrive checkout was the only one that could open an upstream PR.

Check what `main` tracks before reading anything into `git status`. Where it
tracks `origin` and `origin` is upstream, the ahead/behind counts measure the
fork against upstream and say nothing about whether the checkout is current — the
`[ahead 113, behind 1]` seen on 2026-08-18 is that reading, not a health report.

Running only `pwb_toolbox.scraping` and `pwb_toolbox.converting` needs six
packages, not all of `requirements-dev.txt` (which drags in `transformers`,
`datasets`, `scikit-learn`, `scipy`, `matplotlib`, `ccxt` and `ib_insync`):

```
backtrader pandas pytest requests beautifulsoup4 click
```

Verified on Python 3.12 with pandas 3.0.5 — `backtrader` 1.9.78.123 is a 2019
release but runs clean there.

