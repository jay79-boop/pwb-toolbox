# The vault is reachable now, so the rule is direction, not distance

*Decided 2026-08-29.*

**Decision:** a session may attach and read the owner's Obsidian vault
(`jay79-boop/ray-vault`) whenever it needs the vault rather than its rules. It may
**not** push to it. The route, the reasoning and the failure modes are in
[`docs/vault-route.md`](../vault-route.md); the procedure is
`.claude/skills/vault-route/`; `tests/test_vault_boundary.py` fails CI if vault
content lands in this public fork.

**Why now:** the vault became a private GitHub repository, and this session
attached, cloned and read it in about a minute. Everything written here about the
vault two decisions ago rested on the sentence *"a session working in this repo
needs the rules and cannot reach the vault"*. That sentence is now false.

## What survives the premise change, and what does not

Most of it survives, which is worth stating before the correction, because the
correction reads bigger than it is.

`docs/vault-operating-manual.md` **keeps its place as canonical.** A session that
has not attached the vault still cannot see it, and most sessions never will —
paying a clone to learn a rule that fits in a committed file would be a bad trade
in either direction. The split of *what a session needs* from *what the document
contains* was right and is unchanged.

What does not survive is the word **cannot**. It is now *has not*, and the
difference shows up the moment a session needs the vault itself: a daily note, a
staged skill, the Claude config backup, what was actually decided on some date.
Before, that was a dead end and the honest answer was to ask the owner. Now it is
three tool calls, and asking them is a round trip we chose to make — which
[do the work, hand back only what genuinely needs them](../../CLAUDE.md) already
forbids.

## The hazard moved rather than appeared

The 2026-08-29 sync decision protected against a *direction*: vault content
flowing into a public fork. Every guard it produced was aimed at
`tools/obsidian_sync.py`, because that was the only pipe that existed —
`docs/journal/` gitignored, `--commit`/`--push` refusing there.

Attaching the vault opens a second pipe that runs past all of them. Nothing in
that decision stops a session reading a note and pasting it into a doc, because
until today no session could read a note at all. **A guard aimed at a tool
protects the tool's path, not the hazard** — and the hazard was never the tool, it
was the direction. So the new check is written against what arrives here, not
against what sent it.

It draws one line worth keeping: **naming a vault folder is structure; naming a
note inside one is content.** The first draft did not, flagged
`docs/vault-operating-manual.md` on its first run, and would have been "fixed" by
exempting that file — which would have blinded the check inside the one file most
likely to grow a real leak. The rule the failure produced is better than the rule
that was written.

## The write direction, and a fix that is not ours to apply

`nightly-github-sync.ps1` runs at 22:00 on the owner's machine: `git add -A`,
`git commit`, `git push`, and **no `git pull` anywhere in it**. A commit pushed
from a session diverges the branch and that night's backup push is rejected. The
script reports the failure and advises running `git push` in the vault, which
cannot succeed either.

So the read-only rule is not caution, it is arithmetic — and note what it is *not*
grounded in. Pushing is authorised: the credentials carry write access and a
dry-run push authenticates. **The constraint is a script on a machine this session
cannot reach, not a permission**, which is exactly the kind of constraint that
gets missed by checking whether an action is allowed.

Adding `git pull --rebase` ahead of the commit would make two-way writes safe. That
file is on the owner's machine, so it is theirs to apply, and the rule holds until
they do.

## What it cannot catch, said plainly

The guard sees a mirrored folder, the vault's root index files, and a quoted note
path. It does not see prose retyped out of a note with no path on it, and nothing
static could. It is a backstop against the mechanical accidents — a sync followed
by `git add -A`, a snippet pasted with its source attached — and not a licence to
stop thinking about the direction.

That is the same standing this project gave the `.syncignore` interlock: proof
that exclusion was *considered*, which is the most a check can offer. A guard
trusted past its reach is worse than no guard, so its reach is written down next
to it.
