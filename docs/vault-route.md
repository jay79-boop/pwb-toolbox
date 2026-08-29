# The vault route

*Written 2026-08-29.*

The owner's Obsidian vault is a private GitHub repository, `jay79-boop/ray-vault`.
A cloud session can attach and clone it in about a minute. **That is new**, and it
falsifies a premise several documents in this repo were built on.

## What changed

Three files here still read as though the vault were unreachable. The line that
mattered was in
[the vault rules are public; the vault is not](decisions/2026-08-29-the-vault-rules-are-public-the-vault-is-not.md):
*"a session working in this repo needs the rules and cannot reach the vault."*
That is what justified committing `docs/vault-operating-manual.md` as a canonical
public copy of the rules.

The manual keeps its place, and the reasoning for it barely changes — a session
that has not attached the vault still cannot see it, and most never will. What
changes is that "cannot" is now "has not", and the difference matters the moment
a session actually needs the vault rather than its rules.

## The route

Three steps, from a cloud session. There is nothing to install and nothing to set
up on the owner's machine.

1. Attach it: `add_repo` with owner `jay79-boop`, repo `ray-vault`.
2. Clone it, once, inline, with a generous timeout:
   `git clone --depth 1 https://github.com/jay79-boop/ray-vault /home/user/ray-vault`
3. Register it: `register_repo_root` with that directory, so its skills and config
   load on the next turn.

`.claude/skills/vault-route/` carries the procedure and the failure modes.

A **local** session needs none of this. The vault is already on that disk, and
`permissions.additionalDirectories` is what makes it readable — that is Cause 1 in
[working-directories.md](working-directories.md), a different problem with a
different fix.

## What is in there

Roughly 820 files, 18 MB, on `main`. Eleven numbered topic folders, a root index,
and three things worth knowing about before reading anything:

- **A personal half.** A profile, an active legal matter, and the day-by-day
  history of how the vault was built. This is the material the 2026-08-29 decision
  kept out of this public fork, and attaching the vault does not make it publishable.
- **A mirror of the owner's Claude configuration** under `Backups/`: hooks, the
  account-level skills, scheduled tasks, and 111 auto-memory notes. Raw session
  transcripts are **not** there — the vault's own `.gitignore` excludes them by
  name, and the comment above that rule explains why a scrubber was rejected in
  favour of exclusion.
- **The vault automation itself**, as PowerShell. `nightly-github-sync.ps1` is the
  one to read; the next section is about what it does.

## The route is one-way, and this is why

`nightly-github-sync.ps1` runs at 22:00 daily on the owner's machine and does, in
order: `git add -A`, `git commit`, `git push`. **There is no `git pull` anywhere in
it.**

So a commit pushed to `ray-vault` from a cloud session diverges the branch, and
that night's backup push is rejected as non-fast-forward. The script notices and
reports it — but its own remedy text says to run `git push` in the vault, which
will not work either without a pull first. The failure lands on the owner, at
night, in a script they did not touch, phrased as advice that cannot succeed.

**So: read from the vault, never push to it.** Anything that belongs in the vault
gets handed to a local session, which is on the right side of that script.

This is a property of the sync, not a law of nature. Adding `git pull --rebase`
before the `add`/`commit`/`push` would make two-way writes safe — but that file
lives on the owner's machine, so the fix is theirs to apply and the rule above
holds until they do.

The read direction has no such hazard: a shallow clone in a container touches
nothing, and the container is reclaimed with the session.

## The guard, and what it does not reach

`tests/test_vault_boundary.py` fails CI if vault content lands in this repository.
It catches three shapes:

| Shape | What it looks like |
| --- | --- |
| A mirror | tracked paths inside a numbered vault folder |
| A partial mirror | the vault's root index files, by name |
| A snippet | a tracked file quoting a path to a vault *note* |

It draws one distinction deliberately: **naming a vault folder is structure;
naming a note inside one is content.** `docs/vault-operating-manual.md` cannot
state where daily notes go without naming that folder, and it is sanctioned to be
public. A note path underneath one is a different act, and nothing here has any
business citing one.

**What it cannot catch is prose retyped out of a vault note** with no filename and
no path attached. Nothing static can. That half rests on this document and the
skill — which is worth saying out loud, because a guard trusted past its reach is
worse than no guard at all. The same honesty the `.syncignore` interlock was given
in
[a tool that needs a local path should find it](decisions/2026-08-29-a-tool-that-needs-a-local-path-should-find-it.md):
it proves exclusion was *considered*, which is the most a check can do.

## The worked example: what the route is actually good for

The `gexio-machine` skill loads into every session the owner runs, from two
different places: cloud sessions read an uploaded copy from their account, local
sessions read `C:\Users\Gexio\.claude\skills\`. Since the 2026-08-28 nightly,
`backup-claude-config.ps1` mirrors the *machine* copy into this vault repo.

That makes one check possible that was not possible before: **diff what a session
was served against what the machine actually runs.** It found a real split — the
promotion reached the account and never reached the disk, so the two halves of the
fleet spent a day reading different rulebooks: a cloud session knew the
`LastTaskResult` codes, that every console there reports `MainWindowHandle = 0`,
and that a hook registered by bare command name never runs. A local session knew
none of it.

### The half of that check that is not what it looks like

The two sides of the diff are **not** equally trustworthy, and the asymmetry is
easy to miss because both are just files on disk in the container.

| Side | What you are reading | How current |
| --- | --- | --- |
| Account | `~/.claude/skills/synced/<id>/gexio-machine/SKILL.md` | **live** — the store re-syncs mid-session, so re-reading the path is enough |
| Machine | the vault's `Backups/claude-config/skills/…` | **a snapshot**, last written by the 22:00 nightly |

So the machine side lags by up to a day — and on the one day that matters, the day
a skill is promoted, it is *guaranteed* to lag, because the promotion happens after
the previous night's run. Worse, the mirror does not go stale visibly: an unchanged
file is simply not committed, so a mirror that is eleven days old looks exactly like
a mirror that is current.

That is not hypothetical. On 2026-08-29 the mirror read 19,659 bytes while the
account read 22,135 — but the mirrored blob had been untouched since commit
`7c3e438` on **2026-08-18**, and the machine copy had in fact been replaced that
morning, after the last nightly. A session quoting the mirror as "what the machine
runs today" would have reported a drift in the wrong direction, with a real byte
count behind it.

**So date the mirror before you quote it.** One command, and it is not optional:

```bash
git -C /home/user/ray-vault log -1 --format='%h %ad' --date=iso \
  -- Backups/claude-config/skills/gexio-machine/SKILL.md
```

If that date is older than the last change you are asking about, the mirror cannot
answer the question and **no local session can be reached from here to answer it
either** — say so, rather than reporting the snapshot as the state.

That is the local-versus-cloud asymmetry `CLAUDE.md` says has cost days before,
except here it is the file describing the asymmetry that differs.

**Verify this by loading the skill and looking for a phrase, not by counting
lines.** The same file measured 317 lines by `wc -l` and 245 by PowerShell's
`Measure-Object -Line`; a byte count or a distinctive string settles it and a line
count does not.

So the route's value is not that it can install anything — it cannot, and the fix
for the drift above is a copy onto the owner's disk. It is that **a cloud session
is the right instrument for finding drift precisely because it is on the far side
of it.** It reads what it was served and what the machine holds, and neither copy
can hide behind the other.
