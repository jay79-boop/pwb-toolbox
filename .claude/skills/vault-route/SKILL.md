---
name: vault-route
description: Attach the owner's Obsidian vault (the private repo jay79-boop/ray-vault) to a cloud session and read it safely. Load this when a session needs something from the vault rather than just its rules — a daily note, a staged skill, what was decided on some date, the Claude config backup — or when asked to add ray-vault to a session, or when a session reports that it cannot see the vault. Carries the attach-clone-register procedure, the read-only rule and the nightly-sync collision behind it, and the guard that keeps vault content out of this public fork.
---

# Reaching the vault from a session

`jay79-boop/ray-vault` is the owner's Obsidian vault as a private GitHub
repository. `docs/vault-route.md` carries what is in it and why the rules below
exist; this file is what to run.

**First, tell the two cases apart** — they look identical from a chat and the fix
for one does nothing for the other. `docs/working-directories.md` is the full
diagnostic, in one line: a *local* session already has the vault on disk and needs
`permissions.additionalDirectories`; a *cloud* session has no such folder at all
and needs the route below. If the session can `ls` the owner's home directory, it
is local — stop, and do not clone anything.

## The route

1. **Attach.** `add_repo`, owner `jay79-boop`, repo `ray-vault`, access `read`.
   Do not pre-check with `git ls-remote` or a fetch of github.com first — it is
   private, so an unauthenticated probe returns 404 and reads as "does not exist".
2. **Clone.** Once, inline in that turn, nothing else in flight:

   ```bash
   git clone --depth 1 https://github.com/jay79-boop/ray-vault /home/user/ray-vault
   ```

   Give it a generous timeout. On HTTP 429 sleep ten seconds and retry **once** —
   that is this session's own concurrency cap, not a GitHub limit, so a second
   worker makes it worse.
3. **Register.** `register_repo_root` with `/home/user/ray-vault`, then let the
   config arrive as a system-reminder on the next turn rather than reading it.

Shallow is enough for reading. For history — when a change landed, what a commit
touched — use a bounded `git fetch --depth=20 origin main`, never `--unshallow`.

## Read only. Never push

`Backups/claude-config/vault-automation/nightly-github-sync.ps1` runs at 22:00 on
the owner's machine and does `git add -A`, `git commit`, `git push` with **no
`git pull` anywhere in it**. A commit pushed from here diverges the branch, that
night's backup is rejected, and the script's own advice for fixing it cannot
succeed. The cost lands on the owner, at night, in a file they did not touch.

A second mechanism covers the `Backups/` subtree specifically, and it bites even
if a push somehow lands: that tree is refreshed from the machine every night, so an
edit there is overwritten and then committed in its reverted form. The skill's own
"Keeping this current" section states this. Two independent reasons, one rule.

So a push is not a judgement call. Anything that belongs in the vault — a daily
note, a decision, a lesson — goes to a **local** session, which is on the right
side of that script. Say so plainly rather than doing it and reporting after; the
vault's own autonomy rule covers reversible local actions, and this is neither.

## Reading it without leaking it

This fork is public and the vault is not. **Cite the vault by repo name, never by
note path**, and never paste vault prose into a file here.

`tests/test_vault_boundary.py` fails CI on the three shapes it can see — a
mirrored folder, the vault's root index files, a quoted note path. It cannot see
prose retyped with no path on it, so it is a backstop, not permission to stop
thinking. `docs/vault-operating-manual.md` is the one sanctioned public copy of
anything from the vault, and it holds the rules only.

The vault's own operating rules apply while working in it — frontmatter, append
before you create, wikilinks, never archive unprompted. They are in that manual,
which is canonical for them; read it before writing anything, in the unlikely
event a local session ever loads this skill.
