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

## The worked example: a skill this route still cannot install

The vault's Working Style folder holds a staged revision of the `gexio-machine`
skill, dated 2026-08-29 and never installed. Against the copy mirrored under
`Backups/`, it adds about fifty lines of genuinely expensive knowledge: the four
Task Scheduler `LastTaskResult` codes and what each actually means, the finding
that every console here reports `MainWindowHandle = 0` so no scheme can identify a
window by handle, and the rule that a hook registered by bare command name is
listed, accepted, and never runs.

This is the sharpest illustration of the route's shape:

- **Reading it is trivial** — it was found and diffed from a cloud session in
  seconds, which before this route was impossible.
- **Installing it is still out of reach.** The skill loads from `~/.claude/skills/`
  on the owner's Windows machine. No cloud session can write there, and pushing the
  vault's `Backups/` mirror would not install anything — that tree is a backup, and
  the sync that maintains it only ever runs machine → GitHub.

So `CLAUDE.md`'s standing claim is still true, and now for a sharper reason than
"a cloud session cannot durably edit it": the *staging* is durable, the
*installation* is not. Finding one is our job; applying it is theirs.

To install the staged revision, find it rather than assuming a path — the same
rule that governs `tools/obsidian_sync.py`. In PowerShell:

```powershell
$v = Get-ChildItem -Path $HOME -Recurse -Depth 6 -Filter "VAULT-INDEX.md" -File -ErrorAction SilentlyContinue | Select-Object -First 1
```

then look beside it for `gexio-machine-SKILL-proposed-2026-08-29.md`, back up the
current `$HOME\.claude\skills\gexio-machine\SKILL.md`, and copy over it. The
backup is not optional: that description loads into every session on the machine,
so a bad copy is felt everywhere at once.
